"""Real PostgreSQL concurrency/persistence tests, opt in with TEST_DATABASE_URL.
Uses a fresh temporary schema and drops ONLY that schema on completion.
"""
from concurrent.futures import ThreadPoolExecutor
import os
import time
import uuid
import pytest
from sqlalchemy import create_engine, text
from portfolio_advisor.db import Repository
from conftest import add_portfolio

@pytest.fixture
def pg():
    url=os.getenv('TEST_DATABASE_URL')
    if not url:pytest.skip('Set TEST_DATABASE_URL to a disposable PostgreSQL test database.')
    engine=create_engine(url)
    schema='test_portfolio_'+uuid.uuid4().hex
    with engine.begin() as db:db.execute(text('CREATE SCHEMA '+schema))
    repo=Repository(url);repo.engine.dispose()
    repo.engine=create_engine(url,connect_args={'options':f'-csearch_path={schema}'},pool_size=10)
    repo.session.configure(bind=repo.engine);repo.initialize()
    try:yield repo
    finally:
        repo.engine.dispose()
        with engine.begin() as db:db.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        engine.dispose()


def test_concurrent_claim_is_single_and_persisted(pg):
    pid=add_portfolio(pg);r=pg.submit('user-a',pid,str(uuid.uuid4()),{'weights':{'CASH':100}})
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:pg.claim(),range(2)))
    assert sum(x is not None for x in results)==1
    pg.save_state(r.id,'session',{'id':'remote-kept'})
    pg.engine.dispose()
    assert pg.state(r.id)['session']['id']=='remote-kept'
    pg.update(r.id,heartbeat=time.time()-100)
    assert pg.claim().status=='recovering'


def test_concurrent_idempotency_and_user_limit(pg):
    pid=add_portfolio(pg);key=str(uuid.uuid4())
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:pg.submit('user-a',pid,key,{'weights':{'CASH':100}}),range(2)))
    assert results[0].id==results[1].id
    with pytest.raises(RuntimeError):pg.submit('user-a',pid,str(uuid.uuid4()),{'weights':{'CASH':100}})


def test_exclusive_worker_and_cleanup_locks(pg):
    with pg.exclusive('job') as first:
        with pg.exclusive('job') as second:assert first and not second
    with pg.exclusive('job') as after:assert after


def test_deletion_revokes_before_cleanup(pg):
    pid=add_portfolio(pg);r=pg.submit('user-a',pid,str(uuid.uuid4()),{'weights':{'CASH':100}})
    pg.delete('user-a',pid)
    with pytest.raises(LookupError):pg.run('user-a',r.id)
    assert pg.claim() is None
