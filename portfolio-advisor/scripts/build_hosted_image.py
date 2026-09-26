"""Publish a sanitized application snapshot and build it in the dedicated OCI pipeline."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit
import zipfile

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent), str(APP / 'scripts')]
from rollout import Rollout, private_json
from package_hosted_source import package


def git(directory, *arguments):
    result = subprocess.run(['git', '-C', str(directory), *arguments], text=True, capture_output=True,
        env={**os.environ, 'GIT_TERMINAL_PROMPT': '0', 'GIT_ASKPASS': '/usr/bin/false'})
    if result.returncode:
        raise ValueError('Dedicated source repository operation failed; credentials and provider output are suppressed.')
    return result.stdout.strip()


def publish(r):
    o = r.j['outputs']; url = o['source_repository_url']
    archive = APP / 'deploy/private/source.zip'
    snapshot = package(APP.parent, archive)
    if r.j.get('source_snapshot', {}).get('sha256') == snapshot['sha256']:
        return r.j['source_snapshot']
    checkout = APP / 'deploy/private/source-checkout'
    if not checkout.exists():
        checkout.mkdir()
        git(checkout, 'init', '--initial-branch=main')
        git(checkout, 'remote', 'add', 'origin', url)
    if git(checkout, 'remote', 'get-url', 'origin') != url:
        raise ValueError('Source checkout points to a different repository.')
    remote = git(checkout, 'ls-remote', 'origin', 'refs/heads/main')
    if remote:
        git(checkout, 'fetch', 'origin', 'main')
        has_head = subprocess.run(['git', '-C', str(checkout), 'rev-parse', '--verify', 'HEAD'],
                                  capture_output=True).returncode == 0
        if not has_head:
            git(checkout, 'checkout', '-B', 'main', 'origin/main')
        # Never replace changes from another publisher.
        head = git(checkout, 'rev-parse', 'HEAD')
        if head != remote.split()[0]:
            raise ValueError('Dedicated source branch changed; reconcile before publishing.')
    # Remove only previously tracked files in this dedicated export checkout.
    tracked = git(checkout, 'ls-files').splitlines()
    for name in tracked:
        target = checkout / name
        if not target.is_relative_to(checkout): raise ValueError('Invalid source path.')
        target.unlink(missing_ok=True)
    with zipfile.ZipFile(archive) as source:
        source.extractall(checkout)
    git(checkout, 'add', '--all')
    git(checkout, 'commit', '-m', 'Deploy Portfolio Advisor reviewed source ' + snapshot['sha256'][:12])
    commit = git(checkout, 'rev-parse', 'HEAD')
    r.j['source_publish_intent'] = {**snapshot, 'commit': commit, 'repository': url, 'started_at': time.time()}; r.save()
    git(checkout, 'push', 'origin', 'HEAD:refs/heads/main')
    if git(checkout, 'ls-remote', 'origin', 'refs/heads/main').split()[0] != commit:
        raise ValueError('Source push needs reconciliation before a build can start.')
    r.j['source_snapshot'] = {**snapshot, 'commit': commit, 'repository': url}; r.save()
    return r.j['source_snapshot']


def upload_wheel(r):
    wheels = list((APP / 'vendor').glob('oci-*.whl'))
    if len(wheels) != 1: raise ValueError('Expected exactly one reviewed preview OCI SDK wheel.')
    wheel = wheels[0]; digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    o = r.j['outputs']; client = r.oci.object_storage.ObjectStorageClient(r.config, signer=r.signer)
    try:
        existing = client.head_object(o['namespace'], o['build_bucket'], wheel.name)
    except r.oci.exceptions.ServiceError as exc:
        if exc.status != 404: raise
        existing = None
    if existing:
        if existing.headers.get('opc-meta-sha256') != digest:
            raise ValueError('Existing build input has a different checksum; refusing replacement.')
    else:
        r.j['wheel_upload_intent'] = {'name': wheel.name, 'sha256': digest}; r.save()
        with wheel.open('rb') as stream:
            client.put_object(o['namespace'], o['build_bucket'], wheel.name, stream,
                              if_none_match='*', opc_meta={'sha256': digest})
    r.j['build_wheel'] = {'name': wheel.name, 'sha256': digest}; r.save()
    return r.j['build_wheel']


def start(r, attempt=1):
    o = r.j['outputs']; source = r.j['source_snapshot']; wheel = r.j['build_wheel']
    tag = 'source-' + source['commit'][:16]
    client = r.oci.devops.DevopsClient(r.config, signer=r.signer, retry_strategy=r.oci.retry.NoneRetryStrategy())
    key = 'build:' + source['commit'] + ('' if attempt == 1 else ':attempt:' + str(attempt))
    if attempt > 1 and key not in r.j['operations']:
        previous = r.j.get('image_build', {})
        if previous.get('commit') != source['commit']:
            raise ValueError('A retry must use the same previously recorded source commit.')
        run = client.get_build_run(previous['id']).data
        if run.lifecycle_state not in ('FAILED', 'CANCELED'):
            raise ValueError('Only a confirmed failed or canceled build can be retried.')
    if key not in r.j['operations']:
        images = r.oci.pagination.list_call_get_all_results(
            r.oci.artifacts.ArtifactsClient(r.config, signer=r.signer).list_container_images,
            compartment_id=o['compartment_id'], repository_id=o['repository_id']).data
        images = images.items if hasattr(images, 'items') and not isinstance(images, list) else images
        if any(image.version == tag for image in images):
            raise ValueError('Image tag already exists; reconcile its source and digest before proceeding.')
    values = {'APP_BASE_PATH': urlsplit(r.j['applicationURL']).path, 'OCI_NAMESPACE': o['namespace'],
              'BUILD_INPUT_BUCKET': o['build_bucket'], 'PREVIEW_WHEEL_OBJECT': wheel['name'],
              'PREVIEW_WHEEL_SHA256': wheel['sha256'], 'IMAGE_TAG': tag}
    m = r.oci.devops.models
    identifier = r.operation(key, lambda token: client.create_build_run(m.CreateBuildRunDetails(
        display_name='portfolio-advisor-' + source['commit'][:12], build_pipeline_id=o['build_pipeline_id'],
        freeform_tags=r.tags, commit_info=m.CommitInfo(repository_url=o['source_repository_url'],
            repository_branch='main', commit_hash=source['commit']),
        build_run_arguments=m.BuildRunArgumentCollection(items=[m.BuildRunArgument(name=k, value=v) for k,v in values.items()])),
        opc_retry_token=token))
    previous = r.j.get('image_build')
    if previous and previous['id'] != identifier:
        r.j.setdefault('image_build_history', []).append(previous)
    r.j['image_build'] = {'id': identifier, 'commit': source['commit'], 'tag': tag, 'attempt': attempt}; r.save()
    return status(r)


def status(r):
    build = r.j['image_build']; o = r.j['outputs']
    run = r.oci.devops.DevopsClient(r.config, signer=r.signer).get_build_run(build['id']).data
    result = {'id': run.id, 'state': run.lifecycle_state, 'details': run.lifecycle_details,
              'stages': r.oci.util.to_dict(run.build_run_progress)}
    if run.lifecycle_state == 'SUCCEEDED':
        if run.commit_info.commit_hash != build['commit']: raise ValueError('Build source commit differs.')
        images = r.oci.pagination.list_call_get_all_results(
            r.oci.artifacts.ArtifactsClient(r.config, signer=r.signer).list_container_images,
            compartment_id=o['compartment_id'], repository_id=o['repository_id']).data
        images = images.items if hasattr(images, 'items') and not isinstance(images, list) else images
        matches = [x for x in images if x.version == build['tag']]
        if len(matches) != 1: raise ValueError('Successful build has no unique matching registry image.')
        build['digest'] = matches[0].digest; r.save(); result['digest'] = build['digest']
    private_json(APP / 'deploy/private/build-status.json', result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['publish', 'upload-wheel', 'start', 'status'])
    p.add_argument('--review-id')
    p.add_argument('--attempt', type=int, default=1)
    args = p.parse_args()
    if args.action != 'status' and not args.review_id: raise ValueError('Record the rollout authorization.')
    r = Rollout(APP / 'deploy/private/vault-bootstrap.json', 'Sandbox')
    if args.attempt < 1: raise ValueError('Build attempt must be positive.')
    result = start(r, args.attempt) if args.action == 'start' else {'publish': publish, 'upload-wheel': upload_wheel, 'status': status}[args.action](r)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try: main()
    except ValueError as exc: raise SystemExit(str(exc)) from None
    except Exception as exc:
        raise SystemExit(f'Build operation failed ({type(exc).__name__}, HTTP {getattr(exc, "status", None)}); '
                         'reconcile the private journal before resubmitting.') from None
