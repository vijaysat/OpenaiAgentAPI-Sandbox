"""Simulated Agents API contract and integrity tests; these are not live proof."""
from contextlib import contextmanager
import hashlib
import json
from types import SimpleNamespace as NS
import uuid
import pytest
from portfolio_advisor.analytics import calculate
from portfolio_advisor.remote import AgentExecutor, NeedsReview
from conftest import add_portfolio

class Client:
    def __init__(self):
        self.calls=[]; self.events=[]; self.turns=[]; self.items=[]; self.subscribed=False
    @contextmanager
    def stream(self,*args,**kwargs):
        self.subscribed=True;self.calls.append('stream');yield iter(self.events)
    def request(self,method,path,body=None):
        self.calls.append((method,path))
        if path.endswith('/events'):
            assert self.subscribed
            return {}
        if '/turns' in path:return {'data':self.turns}
        if '/items' in path:return {'data':self.items}
        return {'id':'session','status':'idle'}

class Runtime:
    def __init__(self):self.files={}
    def call(self,method,sid,path,*args):
        if method=='write_sandbox_file':self.files[path]=args[0].read().decode()
        return NS(data=NS(content=self.files[path]))


def executor(services):
    settings,repo,_=services;pid=add_portfolio(repo,holdings=[{'symbol':'CASH','quantity':'100','currency':'USD'}])
    run=repo.submit('user-a',pid,str(uuid.uuid4()),{'weights':{'CASH':100}})
    return AgentExecutor(settings,repo,run,Client(),Runtime())


def test_stream_before_post_and_only_new_root_turn(services):
    e=executor(services)
    e.client.turns=[{'id':'old','status':'completed'}]
    e.client.events=[{'type':'agent.session.turn.completed','turn':{'id':'old'}},
        {'type':'agent.session.turn.completed','turn':{'id':'child','subagent_id':'sub'}},
        {'type':'agent.session.turn.completed','turn':{'id':'root'}}]
    e.client.items=[{'type':'message','turn_id':'old','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':'wrong'}]},
                    {'type':'message','turn_id':'root','role':'assistant','phase':'final_answer','content':[{'type':'output_text','text':'verified'}]}]
    assert e.turn({'id':'session'},'prompt')==('root',[],'verified')
    assert e.client.calls[0]=='stream'
    assert e.job_state.load('submission')['priorTurns']==['old']


def test_uncertain_submission_is_never_reposted(services):
    e=executor(services);e.job_state.save('submission',{'priorTurns':[]})
    with pytest.raises(NeedsReview,match='not resent'):e.turn({'id':'session'},'prompt')
    assert not any(isinstance(c,tuple) and c[0]=='POST' for c in e.client.calls)


def test_completed_turn_recovered_without_resubmit(services):
    e=executor(services);e.job_state.save('submission',{'priorTurns':[]})
    e.client.turns=[{'id':'root','status':'completed'}]
    assert e.turn({'id':'session'},'prompt')[0]=='root'
    assert not any(isinstance(c,tuple) and c[0]=='POST' for c in e.client.calls)

@pytest.mark.parametrize('failure',['agent.session.environment.disconnected','agent.session.turn.failed','error'])
def test_failure_retains_submission_evidence(services,failure):
    e=executor(services);e.client.events=[{'type':failure}]
    with pytest.raises(NeedsReview):e.turn({'id':'session'},'prompt')
    assert e.job_state.load('submission')


@pytest.mark.parametrize('recovery', [False, True])
def test_usage_limit_has_safe_actionable_failure_without_replaying_input(services,recovery):
    from geniagent.transport import APIError
    from portfolio_advisor.remote import provider_failure
    e=executor(services)
    error={'code':'usage_limit_exceeded','message':'sk-secret private provider body'}
    if recovery:
        e.job_state.save('submission',{'priorTurns':[]})
        e.client.turns=[{'id':'root','status':'failed','error':error}]
    else:
        e.client.events=[{'type':'error','error':error}]
    with pytest.raises(APIError) as caught:e.turn({'id':'session'},'prompt')
    receipt=provider_failure(caught.value,'agent_running')
    assert receipt['code']=='usage_limit_exceeded'
    assert 'usage or billing limit' in receipt['message']
    assert 'sk-secret' not in json.dumps(receipt)
    assert e.job_state.load('submission')
    assert sum(isinstance(c,tuple) and c[0]=='POST' for c in e.client.calls)==(0 if recovery else 1)


def test_generic_error_reads_new_failed_root_only(services):
    from geniagent.transport import APIError
    e=executor(services)
    e.client.events=[{'type':'error'}]
    original=e.client.request
    def request(method,path,body=None):
        if method=='POST':
            e.client.turns=[{'id':'root','status':'failed','error':{'code':'usage_limit_exceeded'}}]
        return original(method,path,body)
    e.client.request=request
    with pytest.raises(APIError) as caught:e.turn({'id':'session'},'prompt')
    assert caught.value.code=='usage_limit_exceeded'
    assert sum(isinstance(c,tuple) and c[0]=='POST' for c in e.client.calls)==1


def test_unknown_provider_error_body_is_not_displayed(services):
    e=executor(services)
    e.client.events=[{'type':'error','error':{'code':'sk-secret','message':'private body'}}]
    with pytest.raises(NeedsReview) as caught:e.turn({'id':'session'},'prompt')
    assert 'private' not in str(caught.value) and 'sk-secret' not in str(caught.value)


def test_billing_failure_reconciles_after_workspace_expiry_without_oci_or_repost(services):
    from geniagent.transport import APIError
    e=executor(services);e.session=lambda:{'id':'session'}
    e.job_state.save('submission',{'priorTurns':['old']})
    e.client.turns=[{'id':'old','status':'completed'},
        {'id':'failed','status':'failed','error':{'code':'usage_limit_exceeded'}}]
    e.connect=lambda _:pytest.fail('Failed-turn inspection must not reconnect expired compute')
    with pytest.raises(APIError) as caught:e.execute([])
    assert caught.value.code=='usage_limit_exceeded'
    assert not any(isinstance(c,tuple) and c[0]=='POST' for c in e.client.calls)


def test_complete_analysis_with_file_readback(services):
    e=executor(services);e.session=lambda:{'id':'session'};e.connect=lambda _: 'sandbox'
    def turn(session,prompt):
        manifest=e.state.load('manifest');raw=e.runtime.files[manifest['input']]
        request=json.loads(raw)
        result={'requestId':e.run.id,'inputSha256':hashlib.sha256(raw.encode()).hexdigest(),
                'analysis':calculate(request['holdings'],request['weights'],{})}
        e.runtime.files[manifest['output']]=json.dumps(result)
        return 'root',[{'turn_id':'root','command':manifest['command'],'status':'completed','exit_code':0}], 'Verified cash portfolio.'
    e.turn=turn
    result=e.execute([{'symbol':'CASH','quantity':'100','currency':'USD'}])
    assert result['evidence']['commandVerified'] is True
    assert result['evidence']['sandboxId']=='sandbox'
    assert result['analysis']['totalValue']=='100'
    e.runtime.files[e.state.load('manifest')['helper']]+='\n# altered'
    with pytest.raises(NeedsReview,match='integrity'):e.check_files('sandbox',e.state.load('manifest'))


def test_configured_platform_agent_is_reused_not_created(services):
    from portfolio_advisor.remote import AGENT_INSTRUCTIONS
    e=executor(services);e.settings.values['OPENAI_AGENT_ID']='agent-configured'
    e.client.request=lambda method,path,body=None: {'id':'agent-configured','model':'gpt-5.6-luna','instructions':AGENT_INSTRUCTIONS,'tools':[]} if method=='GET' else pytest.fail('Unexpected create')
    assert e.ensure_agent()=='agent-configured'


def test_configured_agent_drift_stops_use(services):
    e=executor(services);e.settings.values['OPENAI_AGENT_ID']='agent-configured'
    e.client.request=lambda *a:{'id':'agent-configured','model':'other','instructions':'changed'}
    with pytest.raises(NeedsReview):e.ensure_agent()


def test_prepared_session_claimed_once_across_portfolios(services):
    from portfolio_advisor.remote import AGENT_INSTRUCTIONS
    settings,repo,_=services
    settings.values.update(OPENAI_AGENT_ID='agent-configured',OPENAI_BOOTSTRAP_SESSION_ID='session-prepared')
    client=Client();posts=[]
    def request(method,path,body=None):
        if path=='/agents/agent-configured':return {'id':'agent-configured','model':'gpt-5.6-luna','instructions':AGENT_INSTRUCTIONS}
        if '/turns' in path or '/items' in path:return {'data':[]}
        if path=='/agents/sessions/session-prepared':return {'id':'session-prepared','agent_id':'agent-configured','model':'gpt-5.6-luna','status':'idle','environment':{'type':'self_hosted'}}
        if method=='POST':posts.append(path);return {'id':'session-new','environment':{'type':'self_hosted'}}
        raise AssertionError(path)
    client.request=request
    first=executor(services);first.client=client
    assert first.session()['id']=='session-prepared'
    repo.finish(first.run.id,'completed')
    second=executor(services);second.client=client
    assert second.session()['id']=='session-new'
    assert posts==['/agents/sessions']


def test_prepared_session_with_wrong_model_is_not_used(services):
    e=executor(services)
    e.settings.values['OPENAI_BOOTSTRAP_SESSION_ID']='session-prepared'
    e.ensure_agent=lambda:'agent-configured'
    e.client.request=lambda *args:{'id':'session-prepared','agent_id':'agent-configured','model':'different-model','status':'idle','environment':{'type':'self_hosted'}}
    with pytest.raises(NeedsReview,match='model'):e.session()
    assert not e.state.load('session')


def test_platform_session_snapshot_awaiting_environment_is_valid():
    from portfolio_advisor.remote import validate_prepared_session,AGENT_INSTRUCTIONS
    session={'id':'prepared','status':'requires_action','agent':{'id':'agent','model':'gpt-5.6-luna','instructions':AGENT_INSTRUCTIONS,'tools':[]},
             'environment':{'id':'env','type':'self_hosted','workspace_directory':'/workspace'},
             'required_actions':[{'type':'environment_connection','environment_id':'env'}]}
    validate_prepared_session(session,'prepared','agent','gpt-5.6-luna')
    session['required_actions']=[{'type':'tool_call','environment_id':'env'}]
    with pytest.raises(NeedsReview):validate_prepared_session(session,'prepared','agent','gpt-5.6-luna')


@pytest.mark.parametrize('correct',[True,False])
def test_preconnected_oci_mapping_is_adopted_before_session_is_saved(services,correct):
    e=executor(services)
    e.settings.values.update(OPENAI_BOOTSTRAP_SESSION_ID='prepared',OCI_SANDBOX_PROJECT_ID='project')
    mapping={'session_id':'prepared' if correct else 'another','environment_id':'env','oci_project_id':'project','sandbox_id':'sandbox','executor_command_id':'command'}
    e.settings.values['OPENAI_BOOTSTRAP_OCI_MAPPING']=json.dumps(mapping)
    e.ensure_agent=lambda:'agent';e.paginated=lambda path:[]
    e.runtime.filename=lambda sid:sid+'-oci.json'
    e.client.request=lambda *a:{'id':'prepared','agent_id':'agent','model':'gpt-5.6-luna','status':'idle','environment':{'id':'env','type':'self_hosted'}}
    if correct:
        assert e.session()['id']=='prepared'
        assert e.state.load('prepared-oci.json')['sandbox_id']=='sandbox'
        assert e.state.load('provision_attempted')['adopted']
    else:
        with pytest.raises(NeedsReview,match='mapping'):e.session()
        assert not e.state.load('session')
        assert not e.state.load('provision_attempted')

@pytest.mark.parametrize('status,outcome',[ (403,'rejected'), (400,'rejected'), (401,'rejected'), (422,'rejected'), (0,'uncertain'), (500,'uncertain'), (409,'uncertain') ])
def test_session_creation_failure_records_receipt_without_provider_body(services,status,outcome):
    from geniagent.transport import APIError
    e=executor(services);e.ensure_agent=lambda:'agent'
    def request(*args):
        raise APIError('sk-secret and private provider body',status,request_id='req_test',code='forbidden')
    e.client.request=request
    with pytest.raises(NeedsReview):e.session()
    state=e.state.load('session_creating')
    assert state['outcome']==outcome
    assert state['failure']['requestId']=='req_test'
    assert 'sk-secret' not in json.dumps(state)
    assert e.state.load('failure')['retryable']==(outcome=='rejected')


def test_explicit_session_rejection_can_be_retried_on_same_run(services):
    from geniagent.transport import APIError
    e=executor(services);e.ensure_agent=lambda:'agent';calls=[]
    def request(*args):
        calls.append(args)
        if len(calls)==1:raise APIError('denied',403)
        return {'id':'created-once','environment':{'type':'self_hosted'}}
    e.client.request=request
    with pytest.raises(NeedsReview):e.session()
    assert e.session()['id']=='created-once'
    assert len(calls)==2
    assert e.state.load('session_creating')['previous']['outcome']=='rejected'


@pytest.mark.parametrize('attempt',[{'at':1,'agentId':'agent'}, {'outcome':'uncertain'}])
def test_legacy_and_uncertain_session_intent_never_replayed(services,attempt):
    e=executor(services);e.state.save('session_creating',attempt)
    e.client.request=lambda *args:pytest.fail('Uncertain request must not be replayed')
    with pytest.raises(NeedsReview,match='unconfirmed'):e.session()


def test_connection_recovery_resumes_recorded_provisioning(services):
    from unittest.mock import Mock
    e=executor(services)
    e.runtime=Mock()
    e.runtime.filename.return_value='session-oci.json'
    e.state.save('provision_attempted',{'at':1})
    e.state.save('session-oci.json',{'sandbox_id':'saved-sandbox'})
    e.client.request=lambda *a:{'status':'connected'}
    assert e.connect({'id':'session','environment':{'id':'env'}})=='saved-sandbox'
    e.runtime.resume.assert_called_once()
    e.runtime.provision.assert_not_called()


def test_oci_timeout_provides_actionable_safe_receipt():
    from portfolio_advisor.remote import provider_failure
    from geniagent.transport import APIError
    error=APIError('private provider body',code='OCI_STARTUP_TIMEOUT',request_id='AAA/BBB/CCC')
    error.provider='OCI'
    result=provider_failure(error,'provisioning_oci')
    assert result['httpStatus'] is None
    assert result['requestId']=='AAA/BBB/CCC'
    assert 'same workspace' in result['message']
    assert 'private' not in json.dumps(result)
