"""Startup recovery reads saved resources without replaying uncertain writes."""
import time
import uuid
from types import SimpleNamespace as NS
from unittest.mock import Mock

import pytest

from geniagent.oci_runtime import OCIRuntime
from portfolio_advisor.api import run_json
from portfolio_advisor.db import DatabaseState
from portfolio_advisor.worker import refresh_startup_status
from conftest import add_portfolio


def stalled(services, owner='user-a'):
    settings, repo, _ = services
    settings.rehearsal = False
    pid = add_portfolio(repo, owner)
    run = repo.submit(owner, pid, str(uuid.uuid4()), {'weights': {'CASH': 100}})
    repo.save_state(run.id, 'session', {'id': 'sess-test'})
    repo.save_state(run.id, 'sess-test-oci.json', {'sandbox_id': 'sbx-existing'})
    repo.save_state(run.id, 'failure', {'code': 'OCI_STARTUP_TIMEOUT'})
    repo.finish(run.id, 'needs_review', error='Old startup message')
    return repo.run(owner, run.id)


def factory(repo, status='STOPPED', error=None):
    sdk = Mock()
    sdk.get_sandbox.return_value = NS(data=NS(lifecycle_state=status), headers={'opc-request-id': 'safe/request'})
    sdk.get_sandbox.side_effect = error
    def build(settings, repository, run):
        return NS(runtime=OCIRuntime(DatabaseState(repo, run.id, None), lambda _: None, sdk, NS()))
    return build, sdk


def test_expired_workspace_is_refreshed_without_creation_or_submission(services):
    run = stalled(services)
    settings, repo, _ = services
    build, sdk = factory(repo)
    refresh_startup_status(settings, repo, build)
    value = run_json(repo.run(run.owner, run.id))
    assert value['workspace']['status'] == 'STOPPED'
    assert value['failure']['code'] == 'OCI_SANDBOX_STOPPED'
    assert value['failure']['requestId'] == 'safe/request'
    assert value['recovery'] == 'workspace_ended'
    assert value['result'] is None and value['status'] == 'needs_review'
    assert 'stopped' in value['error']
    assert [c[0] for c in sdk.mock_calls] == ['get_sandbox']
    refresh_startup_status(settings, repo, build)
    assert sdk.get_sandbox.call_count == 1  # Terminal evidence needs no repeated polling.


def test_running_workspace_can_continue_but_is_not_automatically_submitted(services):
    run = stalled(services)
    settings, repo, _ = services
    build, sdk = factory(repo, 'RUNNING')
    refresh_startup_status(settings, repo, build)
    value = run_json(repo.run(run.owner, run.id))
    assert value['failure']['code'] == 'OCI_WORKSPACE_READY'
    assert value['recovery'] == 'reconcile'
    assert value['status'] == 'needs_review'
    assert [c[0] for c in sdk.mock_calls] == ['get_sandbox']
    refresh_startup_status(settings, repo, build)
    assert sdk.get_sandbox.call_count == 1  # Shared timestamp bounds both workers.


@pytest.mark.parametrize('key', ['submission', 'setup_submitted', 'setup_command_id', 'executor_submitted', 'executor_command_id'])
def test_uncertain_execution_is_not_treated_as_unused_workspace(services, key):
    run = stalled(services)
    settings, repo, _ = services
    if key == 'submission':
        repo.save_state(run.id, key, {'at': 1})
    else:
        repo.save_state(run.id, 'sess-test-oci.json', {'sandbox_id': 'sbx-existing', key: True})
    build, sdk = factory(repo)
    refresh_startup_status(settings, repo, build)
    sdk.get_sandbox.assert_not_called()
    mapping = repo.state(run.id)['sess-test-oci.json']
    repo.save_state(run.id, 'sess-test-oci.json', {**mapping, 'observation': {'status': 'STOPPED'}})
    repo.save_state(run.id, 'failure', {'code': 'OCI_SANDBOX_STOPPED'})
    assert run_json(repo.run(run.owner, run.id))['recovery'] == 'operator_required'


def test_failed_status_read_preserves_last_evidence(services):
    run = stalled(services)
    settings, repo, _ = services
    build, sdk = factory(repo, error=RuntimeError('private provider diagnostic'))
    refresh_startup_status(settings, repo, build)
    value = run_json(repo.run(run.owner, run.id))
    assert value['workspace'] is None
    assert value['failure']['code'] == 'OCI_STARTUP_TIMEOUT'
    assert value['error'] == 'Old startup message'


def test_deleted_portfolio_is_not_polled(services):
    run = stalled(services)
    settings, repo, _ = services
    repo.delete(run.owner, run.portfolio_id)
    build, sdk = factory(repo)
    refresh_startup_status(settings, repo, build)
    sdk.get_sandbox.assert_not_called()


def test_resume_race_is_not_overwritten_by_status_refresh(services):
    run = stalled(services)
    settings, repo, _ = services
    build, sdk = factory(repo)
    response = sdk.get_sandbox.return_value
    def read(_):
        repo.resume(run.owner, run.id)
        return response
    sdk.get_sandbox.side_effect = read
    refresh_startup_status(settings, repo, build)
    saved = repo.run(run.owner, run.id)
    assert saved.status == 'queued' and saved.error is None


def test_ended_workspace_resume_is_rejected_by_api(client, services):
    # This uses the same authenticated owner as the application's local test login.
    _, repo, _ = services
    from portfolio_advisor.db import Login
    from sqlalchemy import select
    with repo.session() as db:
        owner = db.scalar(select(Login.owner))
    run = stalled(services, owner)
    repo.save_state(run.id, 'sess-test-oci.json', {'sandbox_id': 'sbx-existing', 'observation': {'status': 'STOPPED'}})
    assert client.post(f'/api/v1/runs/{run.id}/resume', json={}).status_code == 409
    assert repo.run(owner, run.id).status == 'needs_review'
