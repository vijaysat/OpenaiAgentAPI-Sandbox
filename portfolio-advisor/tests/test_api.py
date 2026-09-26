import json
import uuid
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.responses import Response
from portfolio_advisor.db import Portfolio
from portfolio_advisor.worker import process, cleanup_deleted

CSV=b"symbol,quantity,currency\nAAPL,10,USD\nCASH,100,USD\n"


def upload(client):
    response=client.post('/api/v1/portfolios',files={"file":("holdings.csv",CSV,"text/csv")},data={"name":"My holdings"})
    assert response.status_code==201,response.text
    return response.json()['id']


def test_invalid_upload_no_partial_save(client,services):
    response=client.post('/api/v1/portfolios',files={"file":("bad.csv",b"symbol,quantity,currency\nAAPL,-1,USD")},data={"name":"Bad"})
    assert response.status_code==422
    assert client.get('/api/v1/portfolios').json()==[]


def test_anonymous_and_csrf_protection(app,client):
    with TestClient(app) as anonymous:
        assert anonymous.get('/api/v1/portfolios').status_code==401
    pid=upload(client)
    assert client.delete('/api/v1/portfolios/'+pid,headers={"x-csrf-token":"wrong"}).status_code==403
    assert client.delete('/api/v1/portfolios/'+pid,headers={"origin":"https://attacker.example"}).status_code==403


def test_other_user_cannot_read_run_or_artifact(app,client,services):
    pid=upload(client)
    response=client.post(f'/api/v1/portfolios/{pid}/analyses',json={"weights":{"VTI":60,"BND":35,"CASH":5},"processingAcknowledged":True},headers={"idempotency-key":str(uuid.uuid4())})
    assert response.status_code==202,response.text
    rid=response.json()['id']
    response=app.state.auth.issue('different-user','Other',Response())
    with TestClient(app) as other:
        other.headers['cookie']=response.headers['set-cookie'].split(';')[0]
        for path in [f'/api/v1/portfolios/{pid}',f'/api/v1/runs/{rid}',f'/api/v1/runs/{rid}/events',f'/api/v1/runs/{rid}/artifacts/json']:
            assert other.get(path).status_code==404


def test_full_rehearsal_reports_idempotency_and_delete(client,services):
    settings,repo,store=services
    pid=upload(client); key=str(uuid.uuid4())
    payload={"weights":{"VTI":60,"BND":35,"CASH":5},"processingAcknowledged":True}
    response=client.post(f'/api/v1/portfolios/{pid}/analyses',json=payload,headers={"idempotency-key":key})
    rid=response.json()['id']
    assert client.post(f'/api/v1/portfolios/{pid}/analyses',json=payload,headers={"idempotency-key":key}).json()['id']==rid
    process(settings,repo,store,repo.claim())
    result=client.get(f'/api/v1/runs/{rid}').json()
    assert result['status']=='completed',result
    assert result['result']['mode']=='rehearsal'
    for format in ['html','csv','json']:
        assert client.get(f'/api/v1/runs/{rid}/artifacts/{format}').status_code==200
    assert client.get(f'/api/v1/runs/{rid}/events').status_code==200
    assert client.delete(f'/api/v1/portfolios/{pid}').status_code==202
    assert client.get(f'/api/v1/runs/{rid}/artifacts/json').status_code==404
    cleanup_deleted(settings,repo,store)
    assert client.get('/api/v1/deletions').json()[0]['status']=='completed'


def test_processing_ack_required(client):
    pid=upload(client)
    assert client.post(f'/api/v1/portfolios/{pid}/analyses',json={"weights":{"CASH":100}},headers={"idempotency-key":str(uuid.uuid4())}).status_code==422


def test_new_target_symbol_is_quoted_and_reported_without_changing_holdings(client, services):
    from decimal import Decimal
    settings, repo, store = services
    pid = upload(client)
    before = client.get(f'/api/v1/portfolios/{pid}').json()['holdings']
    response = client.post(f'/api/v1/portfolios/{pid}/analyses',
        json={"weights": {"AAPL": 50, "AMZN": 45, "CASH": 5}, "processingAcknowledged": True},
        headers={"idempotency-key": str(uuid.uuid4())})
    assert response.status_code == 202
    rid = response.json()['id']
    process(settings, repo, store, repo.claim())
    run = client.get(f'/api/v1/runs/{rid}').json()
    assert run['status'] == 'completed'
    analysis = run['result']['analysis']
    added = next(row for row in analysis['rows'] if row['symbol'] == 'AMZN')
    assert added['quantity'] == '0' and added['weight'] == 0
    assert added['targetWeight'] == 45
    assert Decimal(added['quantityChange']) > 0
    assert 'AMZN' in analysis['quotes']
    assert sum(Decimal(row['proposedValue']) for row in analysis['rows']) == Decimal(analysis['totalValue'])
    assert next(Decimal(row['proposedValue']) for row in analysis['rows'] if row['symbol'] == 'CASH') >= 0
    assert 'AMZN' in client.get(f'/api/v1/runs/{rid}/artifacts/csv').text
    assert client.get(f'/api/v1/portfolios/{pid}').json()['holdings'] == before


def test_invalid_added_targets_do_not_create_runs(client):
    pid = upload(client)
    for weights in ({'AAPL': 50, 'BAD SYMBOL': 50}, {'AAPL': 60, 'AMZN': 50}, {'AAPL': 105, 'AMZN': -5}):
        response = client.post(f'/api/v1/portfolios/{pid}/analyses',
            json={'weights': weights, 'processingAcknowledged': True},
            headers={'idempotency-key': str(uuid.uuid4())})
        assert response.status_code == 422
    assert client.get(f'/api/v1/portfolios/{pid}/runs').json() == []
