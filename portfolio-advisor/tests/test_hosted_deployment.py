import io
import json
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from terraform_rollout import archive_module, variables
from portfolio_advisor.auth import Authentication
from portfolio_advisor.config import Settings
from package_hosted_source import entries
from setup_hosted_database import provision_database
from cloud_diagnostics import redact


def test_terraform_archive_excludes_private_state_and_inputs(tmp_path):
    (tmp_path / 'main.tf').write_text('terraform {}')
    (tmp_path / '.terraform.lock.hcl').write_text('# locked')
    for filename in ('.env', 'terraform.tfstate', 'secrets.tfvars', 'provider-binary'):
        (tmp_path / filename).write_text('must-not-upload')
    with zipfile.ZipFile(io.BytesIO(archive_module(tmp_path))) as archive:
        assert set(archive.namelist()) == {'main.tf', '.terraform.lock.hcl'}
        assert all(b'must-not-upload' not in archive.read(name) for name in archive.namelist())


def test_terraform_variables_reject_credentials():
    with pytest.raises(ValueError): variables({'OPENAI_API_KEY': 'synthetic-secret'})
    assert json.loads(variables({'runtime_ids': ['dedicated-id']})['runtime_ids']) == ['dedicated-id']


def test_terraform_reverting_inputs_updates_stack_and_replay_is_idempotent(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS
    import uuid
    import oci
    import terraform_rollout
    from rollout import Rollout

    r = object.__new__(Rollout)
    r.path = tmp_path / 'rollout.json'
    r.j = {'id': str(uuid.uuid4()), 'name': 'test',
           'operations': {'terraform-stack': {'id': 'stack'}},
           'foundation_inputs': {'registry_publish_until': ''}}
    r.tags = {}; r.oci = oci
    current = {}; updates = []; jobs = []

    def update_stack(stack_id, details, **kwargs):
        current.clear(); current.update(details.variables)
        updates.append(dict(current))
        return NS(headers={})

    def create_job(details, **kwargs):
        jobs.append(details)
        return NS(data=NS(id='job-' + str(len(jobs))), headers={})

    client = NS(get_stack=lambda _: NS(data=NS(freeform_tags={'deployment_id': r.j['id']}),
                                       headers={'etag': 'current-version'}),
                list_jobs=lambda **kwargs: NS(data=[]),
                update_stack=update_stack, create_job=create_job)
    monkeypatch.setattr(terraform_rollout, 'client_for', lambda _: client)
    monkeypatch.setattr(terraform_rollout, 'archive_module', lambda _: b'test-module')

    terraform_rollout.plan(r, '1')
    r.j['foundation_inputs']['registry_publish_until'] = '2026-09-25T10:58:14Z'
    terraform_rollout.plan(r, '2')
    r.j['foundation_inputs']['registry_publish_until'] = ''
    terraform_rollout.plan(r, '3')
    assert current['registry_publish_until'] == ''
    assert len(updates) == 3
    terraform_rollout.plan(r, '3')
    assert len(updates) == len(jobs) == 3


def test_oracle_discovery_location_can_differ_from_token_issuer():
    settings = Settings({'AUTH_SESSION_SECRET': 'test-only-secret', 'SSO_CLIENT_ID': 'client',
                         'SSO_CLIENT_SECRET': 'test-client-secret',
                         'SSO_ISSUER_URL': 'https://identity.oraclecloud.com/',
                         'SSO_DISCOVERY_URL': 'https://dedicated.identity.oraclecloud.com/.well-known/openid-configuration'})
    auth = Authentication(settings, None)
    assert auth.oauth.oracle._server_metadata_url == settings.get('SSO_DISCOVERY_URL')


def test_hosted_source_contains_transport_but_excludes_workspace_secrets(tmp_path):
    files = {'LICENSE': 'MIT License\n', 'scripts/api.sh': '#!/bin/bash\n', 'geniagent/transport.py': '# transport',
             'portfolio-advisor/frontend/src/App.tsx': '// UI', '.env': 'SECRET=value',
             'stock-demo/backend/agent.py': '# unrelated',
             'portfolio-advisor/deploy/private/journal.json': 'private',
             'portfolio-advisor/infra/foundation/local.tfvars.json': 'private',
             'portfolio-advisor/frontend/node_modules/dep/index.js': 'cache'}
    for name, value in files.items():
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text(value)
    assert set(dict(entries(tmp_path))) == {'LICENSE', 'scripts/api.sh', 'geniagent/transport.py',
                                          'portfolio-advisor/frontend/src/App.tsx'}


def test_hosted_build_starts_without_a_preview_wheel(monkeypatch):
    import oci
    from types import SimpleNamespace as NS
    from unittest.mock import Mock
    import build_hosted_image

    client = Mock()
    client.create_build_run.return_value = NS(data=NS(id='build_test'))
    monkeypatch.setattr(oci.devops, 'DevopsClient', lambda *a, **kw: client)
    monkeypatch.setattr(oci.artifacts, 'ArtifactsClient', lambda *a, **kw: Mock())
    monkeypatch.setattr(oci.pagination, 'list_call_get_all_results', lambda *a, **kw: NS(data=[]))
    monkeypatch.setattr(build_hosted_image, 'status', lambda r: r.j['image_build'])
    journal = {'outputs': {'compartment_id': 'compartment', 'repository_id': 'repository',
                          'build_pipeline_id': 'pipeline', 'source_repository_url': 'https://example.test/source'},
               'source_snapshot': {'commit': 'a' * 40}, 'operations': {},
               'applicationURL': 'https://example.test/app/'}
    rollout = NS(j=journal, oci=oci, config={}, signer=None, tags={}, save=Mock(),
                 operation=lambda key, action: action('test-token').data.id)
    assert build_hosted_image.start(rollout)['id'] == 'build_test'
    details = client.create_build_run.call_args.args[0]
    assert {item.name: item.value for item in details.build_run_arguments.items} == {
        'APP_BASE_PATH': '/app/', 'IMAGE_TAG': 'source-' + 'a' * 16}


@pytest.mark.parametrize('flags,owner,reason', [
    ((True, False, False, False), ('portfolio_app',), 'elevated privileges'),
    ((False, False, False, False), ('another_owner',), 'another role'),
])
def test_database_setup_stops_on_existing_privilege_or_ownership_drift(flags, owner, reason):
    from types import SimpleNamespace
    statements = []
    def execute(statement, parameters=None):
        statements.append(statement)
        value = (1,) if statement.startswith('SELECT 1') else flags if statement.startswith('SELECT rolsuper') else owner
        return SimpleNamespace(fetchone=lambda: value)
    with pytest.raises(ValueError, match=reason):
        provision_database(SimpleNamespace(execute=execute), 'synthetic-password')
    assert all(statement.startswith('SELECT') for statement in statements)


def test_cloud_log_redaction_removes_credentials():
    values = ['eyJ0ZXN0IjoxfQ.eyJ0ZXN0IjoyfQ.synthetic_signature',
              'sk-synthetic-test-credential', 'Bearer synthetic-secret',
              'postgresql+psycopg://user:synthetic-password@db.example/app']
    output = redact(' '.join(values))
    assert 'synthetic' not in output
    assert 'db.example/app' in output
