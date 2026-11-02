import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import uuid

import oci
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from change_sandbox_network import prepare, change, status
from rollout import Rollout


@pytest.fixture
def rollout(tmp_path):
    r = object.__new__(Rollout)
    r.path = tmp_path / 'journal.json'
    r.oci = oci
    r.j = {'id': str(uuid.uuid4()), 'outputs': {'compartment_id': 'dedicated'},
           'operations': {'project': {'id': 'project'}}}
    m = oci.generative_ai.models
    project = NS(compartment_id='dedicated', freeform_tags={'deployment_id': r.j['id']},
        lifecycle_state='ACTIVE', sandbox_config=m.SandboxConfig(is_enabled=True, is_persistence_enabled=False,
            warm_pool_config=m.SandboxWarmPoolConfig(small_count=0, medium_count=0, large_count=0),
            sandbox_outbound_networking_config=m.ManagedSandboxOutboundNetworkingConfig(
                network_policy=m.SandboxAllowNetworkPolicy(allowed_domains=[
                    m.AllowedDomain(hostnames=['api.openai.com'], ports=[443]),
                    m.AllowedDomain(hostnames=['216.146.35.35'], ports=[53])]))))
    writes = []
    def update(pid, details, **kwargs):
        assert kwargs['if_match'] == 'current-etag'
        assert kwargs['opc_request_id']
        assert isinstance(kwargs['retry_strategy'], oci.retry.NoneRetryStrategy)
        assert 'opc_retry_token' not in kwargs
        # Intent and review must precede every cloud write.
        saved = json.loads(r.path.read_text())
        assert saved['reviews'] and any(k.startswith('network-') for k in saved['operations'])
        writes.append((pid, details))
        project.sandbox_config = copy.deepcopy(details.sandbox_config)
        return NS(headers={'opc-work-request-id': 'work-request'})
    r.client = NS(get_generative_ai_project=lambda _: NS(data=project, headers={'etag': 'current-etag'}),
                  update_generative_ai_project=update)
    return r, project, writes


def test_preparation_is_cloud_read_only_and_restoration_preserves_exact_config(rollout):
    r, project, writes = rollout
    before = oci.util.to_dict(project.sandbox_config)
    plan = prepare(r)
    assert not writes and plan['before'] == before
    assert change(r, 'apply', 'explicit-demo-approval')['status'] == 'submitted'
    assert status(r)['matches_demo_exception']
    assert change(r, 'apply', 'explicit-demo-approval')['status'] == 'verified'
    assert len(writes) == 1
    assert change(r, 'restore', 'approved-rollback')['status'] == 'submitted'
    assert oci.util.to_dict(project.sandbox_config) == before
    assert change(r, 'restore', 'approved-rollback')['status'] == 'verified'
    with pytest.raises(ValueError, match='prior write|already restored'):
        change(r, 'apply', 'old-approval')
    assert len(writes) == 2
    assert r.path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize('problem', ['ownership', 'drift', 'missing_review'])
def test_network_mutation_refuses_invalid_preconditions(rollout, problem):
    r, project, writes = rollout
    prepare(r)
    review = 'approval'
    if problem == 'ownership': project.freeform_tags['deployment_id'] = 'another-deployment'
    elif problem == 'drift': project.sandbox_config.is_persistence_enabled = True
    else: review = None
    with pytest.raises(ValueError): change(r, 'apply', review)
    assert not writes


def test_lost_response_is_not_replayed_and_matching_readback_reconciles(rollout):
    r, project, writes = rollout
    prepare(r)
    update = r.client.update_generative_ai_project
    def timeout(*args, **kwargs):
        update(*args, **kwargs)
        raise TimeoutError('sensitive-provider-body')
    r.client.update_generative_ai_project = timeout
    with pytest.raises(TimeoutError): change(r, 'apply', 'approval')
    assert 'sensitive-provider-body' not in r.path.read_text()
    assert change(r, 'apply', 'approval')['status'] == 'verified'
    assert len(writes) == 1


def test_unconfirmed_write_with_original_state_is_not_replayed(rollout):
    r, _, writes = rollout
    prepare(r)
    def timeout(*args, **kwargs): raise TimeoutError()
    r.client.update_generative_ai_project = timeout
    with pytest.raises(TimeoutError): change(r, 'apply', 'approval')
    with pytest.raises(ValueError, match='prior write'): change(r, 'apply', 'approval')
    assert not writes


def test_network_update_uses_arguments_accepted_by_public_sdk(rollout):
    r, _, _ = rollout
    prepare(r)
    requests = []
    sdk = object.__new__(oci.generative_ai.GenerativeAiClient)
    sdk.retry_strategy = oci.retry.NoneRetryStrategy()
    sdk.base_client = NS(
        get_preferred_retry_strategy=lambda **kwargs: kwargs['operation_retry_strategy'],
        call_api=lambda **kwargs: (requests.append(kwargs) or NS(headers={})))
    r.client.update_generative_ai_project = sdk.update_generative_ai_project
    assert change(r, 'apply', 'approval')['status'] == 'submitted'
    assert len(requests) == 1
    request = requests[0]
    assert request['method'] == 'PUT'
    assert request['header_params']['if-match'] == 'current-etag'
    assert request['header_params']['opc-request-id']
    assert 'opc-retry-token' not in request['header_params']
    assert request['body'].sandbox_config.sandbox_outbound_networking_config.network_policy.mode == 'ALLOW_ALL'
