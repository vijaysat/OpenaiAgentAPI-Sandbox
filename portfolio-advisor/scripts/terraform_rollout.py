"""Run the secret-free Terraform foundation in OCI Resource Manager.

The existing private bootstrap journal is the source of identity and ownership.
Plans, applies and source updates retain intent before the provider request.
"""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import zipfile

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent), str(APP / 'scripts')]
from rollout import Rollout, private_json


def archive_module(directory):
    result = io.BytesIO()
    with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
        files = sorted(directory.glob('*.tf')) + [directory / '.terraform.lock.hcl']
        for path in files:
            info = zipfile.ZipInfo(path.name, (2026, 1, 1, 0, 0, 0))
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    return result.getvalue()


def variables(values):
    allowed = {'region', 'tenancy_id', 'compartment_id', 'prefix', 'vcn_cidr',
               'existing_vault_id', 'existing_key_id', 'runtime_ids', 'secret_ids',
               'postgres_admin_secret_id', 'postgres_ocpus', 'postgres_memory_gb',
               'source_branch', 'registry_host', 'database_setup_client_cidr', 'registry_publish_until'}
    if set(values) - allowed:
        raise ValueError('Only declared non-secret Terraform inputs are accepted.')
    return {k: v if isinstance(v, str) else json.dumps(v) for k, v in values.items()}


def client_for(r):
    return r.oci.resource_manager.ResourceManagerClient(r.config, signer=r.signer,
        retry_strategy=r.oci.retry.NoneRetryStrategy(), timeout=(10, 60))


def plan(r, revision):
    client = client_for(r); m = r.oci.resource_manager.models
    inputs = r.j['foundation_inputs']; payload = archive_module(APP / 'infra/foundation')
    digest = hashlib.sha256(payload + json.dumps(inputs, sort_keys=True).encode()).hexdigest()
    encoded = base64.b64encode(payload).decode()
    stack_id = r.j['operations'].get('terraform-stack', {}).get('id')
    if not stack_id:
        matches = r.oci.pagination.list_call_get_all_results(client.list_stacks,
            compartment_id=inputs['compartment_id'], display_name=r.j['name'] + '-foundation').data
        if matches:
            raise ValueError('A foundation stack already exists; reconcile its identity before creating another.')
        stack_id = r.operation('terraform-stack', lambda token: client.create_stack(m.CreateStackDetails(
            compartment_id=inputs['compartment_id'], display_name=r.j['name'] + '-foundation',
            description='Portfolio Advisor private infrastructure; secret references only',
            terraform_version='1.5.x', freeform_tags=r.tags,
            config_source=m.CreateZipUploadConfigSourceDetails(zip_file_base64_encoded=encoded),
            variables=variables(inputs)), opc_retry_token=token))
    response = client.get_stack(stack_id)
    if response.data.freeform_tags.get('deployment_id') != r.j['id']:
        raise ValueError('Resource Manager stack ownership differs from this deployment.')
    running = client.list_jobs(stack_id=stack_id).data
    if any(job.lifecycle_state in ('ACCEPTED', 'IN_PROGRESS', 'CANCELING') for job in running):
        raise ValueError('A stack job is still running; inspect it before submitting another.')
    # Returning to an older configuration is a new transition (for example,
    # removing a temporary grant). Deduplicate within this revision, not across
    # all historical uses of the same source/inputs.
    r.operation('terraform-source:' + digest + ':' + revision, lambda token: client.update_stack(stack_id,
        m.UpdateStackDetails(config_source=m.UpdateZipUploadConfigSourceDetails(zip_file_base64_encoded=encoded),
                            variables=variables(inputs)), if_match=response.headers['etag'], opc_request_id=token),
        lambda _: stack_id)
    key = 'terraform-plan:' + digest + ':' + revision
    job_id = r.operation(key, lambda token: client.create_job(m.CreateJobDetails(stack_id=stack_id,
        display_name=r.j['name'] + '-plan-' + revision, freeform_tags=r.tags,
        job_operation_details=m.CreatePlanJobOperationDetails()), opc_retry_token=token))
    r.j['terraform_plan'] = {'id': job_id, 'digest': digest, 'revision': revision}; r.save()
    return {'job_id': job_id, 'source_digest': digest, 'stage': 'plan'}


def apply(r, plan_id):
    client = client_for(r); m = r.oci.resource_manager.models
    planned = r.j.get('terraform_plan', {})
    if not plan_id or plan_id != planned.get('id'):
        raise ValueError('Supply the exact reviewed successful plan job ID.')
    payload = archive_module(APP / 'infra/foundation')
    digest = hashlib.sha256(payload + json.dumps(r.j['foundation_inputs'], sort_keys=True).encode()).hexdigest()
    if digest != planned['digest']:
        raise ValueError('Terraform source or inputs changed after planning.')
    job = client.get_job(plan_id).data
    if job.lifecycle_state != 'SUCCEEDED' or job.stack_id != r.j['operations']['terraform-stack']['id']:
        raise ValueError('The reviewed plan has not succeeded for this stack.')
    job_id = r.operation('terraform-apply:' + plan_id, lambda token: client.create_job(m.CreateJobDetails(
        stack_id=job.stack_id, display_name=r.j['name'] + '-apply-' + planned['revision'], freeform_tags=r.tags,
        job_operation_details=m.CreateApplyJobOperationDetails(execution_plan_strategy='FROM_PLAN_JOB_ID',
            execution_plan_job_id=plan_id)), opc_retry_token=token))
    r.j['terraform_apply'] = {'id': job_id, 'plan_id': plan_id}; r.save()
    return {'job_id': job_id, 'stage': 'apply'}


def inspect(r, job_id, logs=False):
    client = client_for(r)
    job_id = job_id or r.j.get('terraform_apply', r.j.get('terraform_plan', {})).get('id')
    if not job_id: raise ValueError('No recorded Terraform job.')
    job = client.get_job(job_id).data
    if job.stack_id != r.j['operations']['terraform-stack']['id']:
        raise ValueError('Job belongs to another stack.')
    result = {'job_id': job_id, 'state': job.lifecycle_state,
              'failure': r.oci.util.to_dict(job.failure_details)}
    if logs:
        entries = r.oci.pagination.list_call_get_all_results(client.get_job_logs, job_id).data
        private_json(APP / 'deploy/private' / (job_id.rsplit('.', 1)[-1] + '-logs.json'),
                     r.oci.util.to_dict(entries))
        result['logs'] = r.oci.util.to_dict(entries)[-100:]
    return result


def outputs(r):
    client = client_for(r); job_id = r.j['terraform_apply']['id']
    if client.get_job(job_id).data.lifecycle_state != 'SUCCEEDED':
        raise ValueError('Foundation apply has not succeeded.')
    entries = client.list_job_outputs(job_id).data
    if not isinstance(entries, list): entries = entries.items
    value = next(x.output_value for x in entries if x.output_name == 'rollout')
    if isinstance(value, str): value = json.loads(value)
    for entry in entries:
        if entry.output_name == 'database_setup_bastion_id':
            value['database_setup_bastion_id'] = entry.output_value
    if value['vault_id'] != r.j['outputs']['vault_id'] or value['key_id'] != r.j['outputs']['key_id']:
        raise ValueError('Foundation references different credential resources.')
    r.j['foundation_outputs'] = value; r.save()
    private_json(APP / 'deploy/private/foundation.json', value)
    return value


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=['plan', 'apply', 'status', 'logs', 'outputs'])
    p.add_argument('--journal', type=Path, default=APP / 'deploy/private/vault-bootstrap.json')
    p.add_argument('--profile', default='Sandbox'); p.add_argument('--review-id')
    p.add_argument('--revision', default='1'); p.add_argument('--plan-id'); p.add_argument('--job-id')
    args = p.parse_args(); r = Rollout(args.journal, args.profile)
    if args.action in ('plan', 'apply') and not args.review_id:
        raise ValueError('Record the deployment authorization before submitting jobs.')
    if args.action == 'plan': result = plan(r, args.revision)
    elif args.action == 'apply': result = apply(r, args.plan_id)
    elif args.action == 'outputs': result = outputs(r)
    else: result = inspect(r, args.job_id, args.action == 'logs')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try: main()
    except ValueError as exc: raise SystemExit(str(exc)) from None
    except Exception as exc:
        raise SystemExit(f'Terraform rollout failed ({type(exc).__name__}, HTTP {getattr(exc, "status", None)}); '
                         'inspect retained journal and job identifiers. Provider bodies are suppressed.') from None
