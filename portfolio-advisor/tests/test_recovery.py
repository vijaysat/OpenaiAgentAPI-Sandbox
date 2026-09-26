import hashlib
import json
import time
import uuid
import pytest
from portfolio_advisor.analytics import calculate
from portfolio_advisor.db import Repository
from portfolio_advisor.remote import verify_envelope, command_matches, NeedsReview
from portfolio_advisor.worker import process, cleanup_deleted
from conftest import add_portfolio


def test_durable_queue_and_claim_recovery(services):
    settings,repo,store=services
    pid=add_portfolio(repo)
    run=repo.submit('user-a',pid,str(uuid.uuid4()),{"weights":{"CASH":100}})
    assert repo.claim().id==run.id
    assert repo.claim() is None
    repo.update(run.id,heartbeat=time.time()-100)
    restarted=Repository(settings.get('DATABASE_URL'))
    recovered=restarted.claim()
    assert recovered.id==run.id and recovered.status=='recovering'


def test_one_active_run_per_user(services):
    _,repo,_=services;pid=add_portfolio(repo)
    repo.submit('user-a',pid,str(uuid.uuid4()),{"weights":{"CASH":100}})
    with pytest.raises(RuntimeError):repo.submit('user-a',pid,str(uuid.uuid4()),{"weights":{"CASH":100}})


def test_integrity_evidence_and_value_reconciliation():
    request={"requestId":"req","holdings":[{"symbol":"CASH","quantity":"100"}],"weights":{"CASH":100}}
    raw=json.dumps(request).encode();analysis=calculate(request['holdings'],request['weights'],{})
    envelope={"requestId":"req","inputSha256":hashlib.sha256(raw).hexdigest(),"analysis":analysis}
    commands=[{"command":"python3 /workspace/helper.py input output","status":"completed","exit_code":0,"turn_id":"turn"}]
    assert verify_envelope(envelope,request,raw,commands,'turn',commands[0]['command'])==analysis
    for bad in ['other-turn',None]:
        with pytest.raises(NeedsReview):verify_envelope(envelope,request,raw,commands,bad,commands[0]['command'])
    envelope['analysis']['totalValue']='999'
    with pytest.raises(NeedsReview):verify_envelope(envelope,request,raw,commands,'turn',commands[0]['command'])


def test_command_wrapper_cannot_smuggle_actions():
    expected='python3 /workspace/helper.py in out'
    assert command_matches("/bin/bash -lc 'python3 /workspace/helper.py in out'",expected)
    assert not command_matches(expected+'; echo unsafe',expected)


def test_provider_failure_does_not_fabricate_report(services):
    settings,repo,store=services;settings.rehearsal=False
    pid=add_portfolio(repo);run=repo.submit('user-a',pid,str(uuid.uuid4()),{"weights":{"CASH":100}});run=repo.claim()
    class Failure:
        def __init__(self,*a): pass
        def execute(self,*a):raise RuntimeError('provider secret must not escape')
    process(settings,repo,store,run,Failure)
    result=repo.run('user-a',run.id)
    assert result.status=='needs_review' and result.result is None
    assert 'secret' not in result.error


def test_cleanup_failure_is_visible_and_retriable(services):
    settings,repo,store=services;pid=add_portfolio(repo);repo.delete('user-a',pid)
    class Failure:
        def delete_prefix(self,*a):raise RuntimeError('unavailable')
    cleanup_deleted(settings,repo,Failure())
    from portfolio_advisor.db import Portfolio
    with repo.session() as db:assert db.get(Portfolio,pid).cleanup=='retrying'
    cleanup_deleted(settings,repo,store)
    with repo.session() as db:assert db.get(Portfolio,pid).cleanup=='completed'


def test_provider_error_preserves_safe_support_metadata(services):
    from geniagent.transport import APIError
    from portfolio_advisor.api import run_json
    settings,repo,store=services;settings.rehearsal=False
    pid=add_portfolio(repo);run=repo.submit('user-a',pid,str(uuid.uuid4()),{'weights':{'CASH':100}});run=repo.claim()
    class Denied:
        def __init__(self,*a):pass
        def execute(self,*a):raise APIError('private provider body',403,request_id='req_test',code='forbidden')
    process(settings,repo,store,run,Denied)
    value=run_json(repo.run('user-a',run.id))
    assert value['failure']=={'stage':'checking_cloud_session','httpStatus':403,'requestId':'req_test','code':'forbidden'}
    assert 'private provider body' not in json.dumps(value)
    assert 'Models read' in value['error']


def test_known_rejected_creation_does_not_block_deletion_cleanup(services):
    settings,repo,store=services
    pid=add_portfolio(repo);run=repo.submit('user-a',pid,str(uuid.uuid4()),{'weights':{'CASH':100}})
    repo.save_state(run.id,'session_creating',{'outcome':'rejected','failure':{'httpStatus':403}})
    repo.finish(run.id,'needs_review');repo.delete('user-a',pid)
    settings.rehearsal=False
    from unittest.mock import patch
    from portfolio_advisor.db import Portfolio
    with patch('portfolio_advisor.worker.AgentExecutor'):
        cleanup_deleted(settings,repo,store)
    with repo.session() as db:assert db.get(Portfolio,pid).cleanup=='completed'


def test_legacy_uncertain_creation_still_blocks_cleanup(services):
    settings,repo,store=services
    pid=add_portfolio(repo);run=repo.submit('user-a',pid,str(uuid.uuid4()),{'weights':{'CASH':100}})
    repo.save_state(run.id,'session_creating',{'at':1})
    repo.finish(run.id,'needs_review');repo.delete('user-a',pid);settings.rehearsal=False
    from unittest.mock import patch
    from portfolio_advisor.db import Portfolio
    with patch('portfolio_advisor.worker.AgentExecutor'):
        cleanup_deleted(settings,repo,store)
    with repo.session() as db:assert db.get(Portfolio,pid).cleanup=='retrying'
