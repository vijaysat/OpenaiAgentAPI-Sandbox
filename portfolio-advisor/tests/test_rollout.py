import json
from pathlib import Path
import sys
from types import SimpleNamespace as NS
import uuid
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from rollout import Rollout, private_json


def journal(tmp_path):
    r=object.__new__(Rollout);r.path=tmp_path/'rollout.json';r.j={'id':str(uuid.uuid4()),'operations':{}}
    return r


def test_uncertain_cloud_write_is_journaled_and_not_repeated(tmp_path):
    r=journal(tmp_path);calls=[]
    def fail(token):calls.append(token);raise TimeoutError('sensitive provider body')
    with pytest.raises(TimeoutError):r.operation('create',fail)
    saved=json.loads(r.path.read_text());assert saved['operations']['create']['retryToken']==calls[0]
    assert 'sensitive' not in r.path.read_text()
    with pytest.raises(ValueError,match='unconfirmed'):r.operation('create',fail)
    assert len(calls)==1
    assert r.path.stat().st_mode & 0o777==0o600


def test_known_cloud_resource_is_reused(tmp_path):
    r=journal(tmp_path)
    assert r.operation('create',lambda token:NS(data=NS(id='resource-test'),headers={}))=='resource-test'
    assert r.operation('create',lambda _:pytest.fail('duplicate creation'))=='resource-test'


def test_journal_refuses_to_overwrite_another_process(tmp_path):
    r=journal(tmp_path);r.save()
    concurrent=json.loads(r.path.read_text());concurrent['new_evidence']='retained'
    private_json(r.path,concurrent)
    with pytest.raises(ValueError,match='another process'):r.save()
    assert json.loads(r.path.read_text())['new_evidence']=='retained'


def test_sdk_project_model_has_restricted_egress(tmp_path):
    import oci
    r=journal(tmp_path);r.oci=oci;r.tags={};r.j.update(name='test',outputs={'compartment_id':'test-compartment'})
    captured=[]
    r.client=NS(create_generative_ai_project=lambda details,**kw:(captured.append(details) or NS(data=NS(id='project-test'),headers={})))
    assert r.project()=='project-test'
    policy=captured[0].sandbox_config.sandbox_outbound_networking_config.network_policy
    assert policy.mode=='ALLOW'
    assert policy.allowed_domains[0].hostnames==['api.openai.com','codex-cloud-environments.chatgpt.com',
                                               'registry.npmjs.org','query1.finance.yahoo.com']


@pytest.mark.parametrize('registry_digest,expected', [('sha256:reviewed','sha256:reviewed'), ('sha256:changed',None)])
def test_status_requires_current_registry_digest(tmp_path,registry_digest,expected):
    r=journal(tmp_path);r.config={};r.signer=None
    r.j.update(outputs={'compartment_id':'dedicated','repository_id':'repo'},
               operations={'deployment':{'id':'deployment'}},
               expectedImage={'artifactId':'artifact','containerUri':'registry/repo','tag':'immutable','digest':'sha256:reviewed'})
    artifact=NS(id='artifact',container_uri='registry/repo',tag='immutable',status='ACTIVE')
    r.client=NS(get_hosted_deployment=lambda _:NS(data=NS(lifecycle_state='ACTIVE',active_artifact=artifact)))
    r.oci=NS(artifacts=NS(ArtifactsClient=lambda *a,**kw:NS(list_container_images=lambda **kwargs:None)),
             pagination=NS(list_call_get_all_results=lambda *a,**kw:NS(data=[NS(version='immutable',digest=registry_digest)])))
    assert r.status()['verifiedDigest']==expected


def test_pending_initial_deployment_does_not_add_a_duplicate_artifact(tmp_path):
    r=journal(tmp_path);r.config={};r.signer=None
    digest='sha256:'+'1'*64
    r.j.update(outputs={'registry_host':'registry','namespace':'tenant','repository_name':'advisor',
                        'repository_id':'repository','compartment_id':'dedicated'})
    r.oci=NS(artifacts=NS(ArtifactsClient=lambda *a,**kw:NS(list_container_images=lambda **kwargs:None)),
        generative_ai=NS(models=NS()),
        pagination=NS(list_call_get_all_results=lambda *a,**kw:NS(data=[NS(version='source-test',digest=digest)])))
    r.prepare_deployment=lambda *args:'new-deployment'
    r.client=NS(get_hosted_deployment=lambda _:NS(data=NS(lifecycle_state='CREATING')),
                add_artifact=lambda *a,**kw:pytest.fail('duplicate artifact'))
    with pytest.raises(ValueError,match='pending'):
        r.deploy('registry/tenant/advisor','source-test',digest)
    assert r.j['previousArtifact'] is None
    assert r.j['expectedImage']['digest']==digest


@pytest.mark.parametrize('retry_failed,request_state,attempt,updates', [
    (False, 'FAILED', 1, 0), (True, 'IN_PROGRESS', 1, 0), (True, 'FAILED', 1, 1),
    (True, 'IN_PROGRESS', 2, 0), (True, 'FAILED', 2, 1), (True, 'FAILED', 3, 0),
])
def test_failed_initial_artifact_requires_explicit_reconciled_retry(tmp_path,retry_failed,request_state,attempt,updates):
    import oci
    r=journal(tmp_path);r.config={};r.signer=None;captured=[]
    digest='sha256:'+'2'*64;uri='registry/tenant/advisor'
    r.j.update(outputs={'registry_host':'registry','namespace':'tenant','repository_name':'advisor',
                        'repository_id':'repository','compartment_id':'dedicated'},
               operations={'application':{'id':'application'},'deployment':{'id':'deployment','workRequest':'creation-request'}},
               expectedImage={'digest':digest})
    if attempt > 1:
        r.j['operations']['activate:'+digest]={'id':'artifact','workRequest':'first-activation-request'}
    r.oci=NS(artifacts=NS(ArtifactsClient=lambda *a,**kw:NS(list_container_images=lambda **kwargs:None)),
        generative_ai=oci.generative_ai,retry=oci.retry,
        pagination=NS(list_call_get_all_results=lambda *a,**kw:NS(data=[NS(version='source-test',digest=digest)])))
    r.prepare_deployment=lambda *args:'deployment'
    artifact=NS(id='artifact',container_uri=uri,tag='source-test',status='FAILED')
    r.client=NS(get_hosted_deployment=lambda _:NS(data=NS(lifecycle_state='NEEDS_ATTENTION',
                    active_artifact=None,artifacts=[artifact]),headers={'etag':'current-version'}),
        get_work_request=lambda _:NS(data=NS(status=request_state)),
        get_hosted_application=lambda _:NS(data=NS(lifecycle_state='ACTIVE')),
        update_hosted_deployment=lambda *args,**kw:(captured.append((args,kw)) or NS(headers={})),
        add_artifact=lambda *a,**kw:pytest.fail('must reuse the registered artifact'))
    if updates:
        r.deploy(uri,'source-test',digest,retry_failed=True,activation_attempt=attempt)
        r.deploy(uri,'source-test',digest,retry_failed=True,activation_attempt=attempt)
        assert captured[0][0][1].active_artifact.id=='artifact'
        assert captured[0][1]['if_match']=='current-version'
    else:
        with pytest.raises(ValueError,match='failed|FAILED'):
            r.deploy(uri,'source-test',digest,retry_failed=retry_failed,activation_attempt=attempt)
    assert len(captured)==updates


def test_sync_references_preserves_config_and_writes_only_ids(tmp_path,monkeypatch):
    import rollout
    app=tmp_path/'app';app.mkdir();env=tmp_path/'.env'
    env.write_text('OPENAI_MODEL=gpt-5.6-luna\nOPENAI_API_KEY={"secretId":""}\n')
    monkeypatch.setattr(rollout,'APP',app)
    r=journal(tmp_path)
    r.j.update(outputs={'compartment_id':'compartment','region':'region','vault_id':'vault','key_id':'key'},
               secrets={'AUTH_SESSION_SECRET':'ocid1.vaultsecret.test.session'})
    r.sync_references()
    values=rollout.read_env(env)
    assert values['OPENAI_MODEL']=='gpt-5.6-luna'
    assert json.loads(values['AUTH_SESSION_SECRET'])=={'secretId':'ocid1.vaultsecret.test.session'}
    assert json.loads(values['OPENAI_API_KEY'])=={'secretId':''}
    assert env.stat().st_mode & 0o777==0o600


@pytest.mark.parametrize('matches',[True,False])
def test_import_app_key_removes_plaintext_only_after_verified_current(tmp_path,monkeypatch,matches):
    import base64
    import rollout
    app=tmp_path/'app';app.mkdir();env=tmp_path/'.env'
    supplied='synthetic-test-key'
    env.write_text('OPENAI_AGENT_APIKEY='+supplied+'\nOPENAI_MODEL=gpt-5.6-luna\n')
    monkeypatch.setattr(rollout,'APP',app)
    r=journal(tmp_path);r.config={};r.signer=None
    r.j.update(outputs={'compartment_id':'compartment','region':'region'},secrets={})
    r.verify_application_key=lambda *a:None
    def seed(name,value):
        assert value==supplied
        r.j['secrets'][name]='ocid1.vaultsecret.test.application'
        return r.j['secrets'][name]
    r.seed=seed
    bundle=NS(data=NS(secret_bundle_content=NS(content=base64.b64encode((supplied if matches else 'different').encode()).decode())))
    r.oci=NS(secrets=NS(SecretsClient=lambda *a,**kw:NS(get_secret_bundle=lambda *a,**kw:bundle)))
    if matches:
        r.import_app_key()
        assert supplied not in env.read_text()
        assert 'OPENAI_AGENT_APIKEY' not in rollout.read_env(env)
    else:
        with pytest.raises(ValueError,match='differs'):r.import_app_key()
        assert rollout.read_env(env)['OPENAI_AGENT_APIKEY']==supplied


@pytest.mark.parametrize('status',[401,403])
def test_invalid_application_key_does_not_mutate_vault(tmp_path,monkeypatch,status):
    import rollout
    from geniagent import transport
    app=tmp_path/'app';app.mkdir();env=tmp_path/'.env'
    env.write_text('OPENAI_APPLICATION_KEY=synthetic-invalid\nOPENAI_PROJECT_ID=project\nOPENAI_AGENT_ID=agent\n')
    monkeypatch.setattr(rollout,'APP',app)
    def rejected(*a,**kw):raise transport.APIError('sensitive provider message',status=status)
    monkeypatch.setattr(transport,'CurlClient',lambda *a,**kw:NS(request=rejected))
    r=journal(tmp_path)
    with pytest.raises(ValueError,match=f'HTTP {status}') as error:r.import_app_key()
    assert 'sensitive' not in str(error.value)
    if status==403:assert 'api.agents.read' in str(error.value)
    assert r.j['operations']=={}
    assert 'OPENAI_APPLICATION_KEY' in rollout.read_env(env)


def test_configuration_update_does_not_record_or_submit_activation(tmp_path):
    import oci
    r=journal(tmp_path);r.config={};r.signer=None
    digest='sha256:'+'3'*64;uri='registry/tenant/advisor'
    r.j.update(outputs={'registry_host':'registry','namespace':'tenant','repository_name':'advisor',
                       'repository_id':'repository','compartment_id':'dedicated'},
               operations={'application':{'id':'application'},'deployment':{'id':'deployment'}},
               expectedImage={'digest':digest})
    artifact=NS(id='target-artifact',container_uri=uri,tag='access-test',status='INACTIVE')
    r.oci=NS(artifacts=NS(ArtifactsClient=lambda *a,**kw:NS(list_container_images=lambda **kwargs:None)),
        generative_ai=oci.generative_ai,retry=oci.retry,
        pagination=NS(list_call_get_all_results=lambda *a,**kw:NS(data=[NS(version='access-test',digest=digest)])))
    r.prepare_deployment=lambda *args:'deployment'
    r.client=NS(get_hosted_deployment=lambda _:NS(data=NS(lifecycle_state='ACTIVE',active_artifact=None,artifacts=[artifact]),headers={'etag':'version'}),
                get_hosted_application=lambda _:NS(data=NS(lifecycle_state='UPDATING')),
                update_hosted_deployment=lambda *a,**kw:pytest.fail('must wait for configuration'))
    with pytest.raises(ValueError,match='configuration is pending'):
        r.deploy(uri,'access-test',digest)
    assert 'activate:'+digest not in r.j['operations']
    assert r.j['expectedImage']['artifactId']=='target-artifact'


@pytest.mark.parametrize('problem', [None, 'owner', 'compartment', 'settings', 'state', 'sandbox', 'exit', 'output'])
def test_project_repair_requires_owned_matching_project_and_execution(tmp_path, problem):
    import oci
    r=journal(tmp_path);r.config={};r.signer=None
    original={'id':'original-project','retryToken':'original-token'}
    r.j.update(outputs={'compartment_id':'dedicated','region':'us-chicago-1'},
               operations={'project':original.copy()})
    config=oci.generative_ai.models.SandboxConfig(is_enabled=True,is_persistence_enabled=False)
    current=NS(compartment_id='dedicated',freeform_tags={'deployment_id':r.j['id']},sandbox_config=config)
    target=NS(compartment_id='dedicated',freeform_tags={'deployment_id':r.j['id']},
              sandbox_config=config,lifecycle_state='ACTIVE')
    sandbox=NS(project_id='replacement-project')
    command=NS(exit_code=0);output=NS(stdout='OCI_STARTUP_READY 3.11.13\n')
    if problem=='owner':target.freeform_tags={}
    if problem=='compartment':target.compartment_id='other'
    if problem=='settings':target.sandbox_config=oci.generative_ai.models.SandboxConfig(is_enabled=True,is_persistence_enabled=True)
    if problem=='state':target.lifecycle_state='CREATING'
    if problem=='sandbox':sandbox.project_id='other-project'
    if problem=='exit':command.exit_code=None
    if problem=='output':output.stdout='unverified'
    r.client=NS(get_generative_ai_project=lambda pid:NS(data=current if pid=='original-project' else target))
    sdk=NS(get_sandbox=lambda _:NS(data=sandbox),get_sandbox_command=lambda *a:NS(data=command),
           get_sandbox_command_output=lambda *a:NS(data=output))
    r.oci=NS(util=oci.util,retry=oci.retry,generative_ai_sandbox=NS(GenerativeAiSandboxClient=lambda *a,**kw:sdk))
    if problem:
        with pytest.raises(ValueError):r.select_sandbox_project('replacement-project','sandbox','command')
        assert 'activeSandboxProject' not in r.j
        assert r.sandbox_project_id()=='original-project'
    else:
        r.select_sandbox_project('replacement-project','sandbox','command')
        assert r.sandbox_project_id()=='replacement-project'
        assert r.project()=='replacement-project'
        r.select_sandbox_project('replacement-project','sandbox','command')
        assert len(r.j['sandboxProjectHistory'])==1
    assert r.j['operations']['project']==original


def test_environment_regeneration_keeps_verified_replacement_and_secret_references(tmp_path,monkeypatch):
    import rollout
    app=tmp_path/'app';app.mkdir();monkeypatch.setattr(rollout,'APP',app)
    r=journal(tmp_path)
    r.j.update(outputs={'compartment_id':'dedicated','region':'us-chicago-1','namespace':'tenant','bucket':'private'},
        operations={'project':{'id':'original-project'}},
        activeSandboxProject={'id':'replacement-project','verifiedCommandId':'command'},
        secrets={name:'ocid1.vaultsecret.test.'+name for name in rollout.SECRET_NAMES},
        applicationURL='https://example.test/app',model='gpt-5.6-luna',
        sso={'issuer':'https://sso.test','clientId':'client'})
    r.environment('openai-project')
    r.sync_references()
    local=rollout.read_env(tmp_path/'.env')
    hosted=json.loads((tmp_path/'hosted-env.json').read_text())
    assert local['OCI_SANDBOX_PROJECT_ID']==hosted['OCI_SANDBOX_PROJECT_ID']=='replacement-project'
    assert local['OCI_AUTH_MODE']=='security_token'
    assert hosted['OCI_AUTH_MODE']=='resource_principal'
    assert all(json.loads(hosted[name])=={'secretId':sid} for name,sid in r.j['secrets'].items())
    assert (tmp_path/'.env').stat().st_mode & 0o777==0o600
    assert r.j['operations']['project']['id']=='original-project'
