"""Prepare, apply and restore a separately approved demo network exception.

Preparation is read-only in OCI. The original policy and sandbox settings remain
in the private rollout journal. This does not change new-project defaults.
"""
import argparse
import copy
import json
from pathlib import Path
import sys
import time
import uuid

APP = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(APP), str(APP.parent), str(APP / 'scripts')]
from rollout import Rollout

PLAN_KEY = 'demo_allow_all'


def project_response(rollout):
    pid = rollout.sandbox_project_id()
    response = rollout.client.get_generative_ai_project(pid)
    project = response.data
    if (project.compartment_id != rollout.j['outputs']['compartment_id']
            or (project.freeform_tags or {}).get('deployment_id') != rollout.j['id']):
        raise ValueError('Dedicated project ownership could not be verified.')
    return pid, response


def prepare(rollout):
    pid, response = project_response(rollout)
    project = response.data
    if project.lifecycle_state != 'ACTIVE':
        raise ValueError('Project must be ACTIVE before preparing a change.')
    before = rollout.oci.util.to_dict(project.sandbox_config)
    network = before['sandbox_outbound_networking_config']
    if network['network_mode'] != 'MANAGED' or network['network_policy']['mode'] != 'ALLOW':
        raise ValueError('This workflow expects the existing managed ALLOW policy.')
    changes = rollout.j.setdefault('network_changes', {})
    if PLAN_KEY in changes:
        plan = changes[PLAN_KEY]
        if plan['project_id'] != pid or plan['before'] != before:
            raise ValueError('A saved plan exists; inspect status and reconcile it before changing anything.')
        return plan
    after = copy.deepcopy(before)
    after['sandbox_outbound_networking_config']['network_policy'] = {'mode': 'ALLOW_ALL'}
    plan = {'id': str(uuid.uuid4()), 'project_id': pid, 'prepared_at': time.time(),
            'before': before, 'after': after,
            'scope': 'Dedicated demo project; unrestricted outbound access until explicitly restored.',
            'approval_required': True}
    changes[PLAN_KEY] = plan
    rollout.save()
    return plan


def status(rollout):
    pid, response = project_response(rollout)
    actual = rollout.oci.util.to_dict(response.data.sandbox_config)
    plan = rollout.j.get('network_changes', {}).get(PLAN_KEY, {})
    return {'project_id': pid, 'state': response.data.lifecycle_state,
            'sandbox_config': actual, 'matches_original': actual == plan.get('before'),
            'matches_demo_exception': actual == plan.get('after')}


def change(rollout, action, review_id):
    if action not in ('apply', 'restore') or not review_id:
        raise ValueError('Supply apply/restore and the explicit approval review ID.')
    plan = rollout.j.get('network_changes', {}).get(PLAN_KEY)
    if not plan:
        raise ValueError('Prepare and review the exact network change first.')
    pid, response = project_response(rollout)
    if plan['project_id'] != pid:
        raise ValueError('Saved network plan belongs to a different project.')
    current = rollout.oci.util.to_dict(response.data.sandbox_config)
    source, target = (plan['before'], plan['after']) if action == 'apply' else (plan['after'], plan['before'])
    operation = f'network-{action}:{plan["id"]}'
    previous = rollout.j['operations'].get(operation)
    if current == target:
        if previous:
            if response.data.lifecycle_state == 'ACTIVE':
                previous.update(id=pid, reconciled='matching_active_configuration', verified_at=time.time())
                rollout.save()
                return {'project_id': pid, 'status': 'verified', 'action': action}
            return {'project_id': pid, 'status': 'pending', 'action': action}
        raise ValueError('Policy changed outside this journal; reconcile it before continuing.')
    if current != source or response.data.lifecycle_state != 'ACTIVE':
        raise ValueError('Project configuration differs from the reviewed source or is not ACTIVE.')
    if previous:
        raise ValueError('A prior write is unresolved or no longer matches; no request was repeated.')
    if action == 'apply' and rollout.j['operations'].get(f'network-restore:{plan["id"]}'):
        raise ValueError('This exception was already restored; a new reviewed plan is required.')
    model = rollout.oci.generative_ai.models
    policy = target['sandbox_outbound_networking_config']['network_policy']
    if policy['mode'] == 'ALLOW_ALL':
        policy_model = model.SandboxAllowAllNetworkPolicy()
    else:
        policy_model = model.SandboxAllowNetworkPolicy(allowed_domains=[
            model.AllowedDomain(hostnames=rule['hostnames'], ports=rule['ports'])
            for rule in policy['allowed_domains']])
    config = copy.deepcopy(response.data.sandbox_config)
    config.sandbox_outbound_networking_config = model.ManagedSandboxOutboundNetworkingConfig(network_policy=policy_model)
    if rollout.oci.util.to_dict(config) != target:
        raise ValueError('SDK configuration does not match the reviewed target.')
    rollout.j.setdefault('reviews', []).append({'id': review_id, 'operation': operation, 'at': time.time()})
    rollout.save()
    # Project updates accept a request correlation ID, not a create retry token.
    # The ETag and retained intent guard the single attempt; uncertain writes
    # must still be reconciled through readback instead of being resubmitted.
    rollout.operation(operation, lambda token: rollout.client.update_generative_ai_project(pid,
        model.UpdateGenerativeAiProjectDetails(sandbox_config=config),
        if_match=response.headers['etag'], opc_request_id=token,
        retry_strategy=rollout.oci.retry.NoneRetryStrategy()), lambda _: pid)
    return {'project_id': pid, 'status': 'submitted', 'action': action,
            'next': 'Read status until ACTIVE and matching; an accepted update is not network proof.'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'apply', 'restore', 'status'])
    parser.add_argument('--journal', type=Path, default=APP / 'deploy/private/vault-bootstrap.json')
    parser.add_argument('--profile', default='Sandbox')
    parser.add_argument('--review-id')
    args = parser.parse_args()
    rollout = Rollout(args.journal, args.profile)
    if args.action == 'prepare': result = prepare(rollout)
    elif args.action == 'status': result = status(rollout)
    else: result = change(rollout, args.action, args.review_id)
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    try: main()
    except ValueError as exc: raise SystemExit(str(exc)) from None
    except Exception as exc:
        status_code = getattr(exc, 'status', None)
        raise SystemExit(f'Network operation failed (HTTP {status_code}); retained identifiers require reconciliation. '
                         'Provider bodies and credentials are suppressed.') from None
