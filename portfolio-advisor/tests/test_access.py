"""Authorization tests use synthetic identities and isolated databases only."""
import hashlib
import time
import uuid
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from starlette.responses import Response
from portfolio_advisor.access import AccessControl
from portfolio_advisor.api import create_app
from portfolio_advisor.auth import Authentication
from portfolio_advisor.config import Settings
from portfolio_advisor.db import AccessAudit, AppUser, Login, Record, Repository
from conftest import add_portfolio

ISSUER = 'https://identity.example.test'


def live_app(services, **values):
    settings, repo, store = services
    settings = Settings({**settings.values, 'SSO_ISSUER_URL': ISSUER,
                         'SSO_CLIENT_ID': 'fixture', 'SSO_CLIENT_SECRET': 'fixture', **values})
    return create_app(settings, repo, store)


def signed_in(app, owner, name='Synthetic user'):
    response = app.state.auth.issue(owner, name, Response())
    client = TestClient(app)
    client.headers['cookie'] = response.headers['set-cookie'].split(';')[0]
    client.headers['x-csrf-token'] = client.get('/api/me').json()['csrf']
    return client


@pytest.fixture
def admin(services):
    _, repo, _ = services
    access = AccessControl(repo)
    access.bootstrap('admin@example.test', owner='admin-owner', name='Demo admin')
    app = live_app(services)
    with signed_in(app, 'admin-owner') as client:
        yield app, client, access


def grant(client, email='member@example.test', **values):
    response = client.post('/api/v1/admin/users', json={'name': 'New member', 'email': email, **values})
    assert response.status_code == 201, response.text
    return response.json()


def test_grant_bind_sign_in_and_immediate_revoke(admin, services):
    app, client, access = admin
    person = grant(client)
    access.authorize_sso('member-owner', {'email': 'MEMBER@example.test', 'email_verified': True})
    with signed_in(app, 'member-owner') as member:
        assert member.get('/api/me').json()['role'] == 'member'
        for path in ('/api/v1/admin/users', '/api/v1/admin/access-history'):
            assert member.get(path).status_code == 403
        assert member.post('/api/v1/admin/users', json={'email': 'self@example.test'}).status_code == 403
        changed = client.patch('/api/v1/admin/users/'+person['id'], json={**{k:person[k] for k in ('email','name','role','version')}, 'active':False})
        assert changed.status_code == 200
        for path in ('/api/me','/api/v1/portfolios','/api/v1/templates'):
            assert member.get(path).status_code == 401
        # Re-enabling cannot revive the old session.
        disabled = changed.json()
        assert client.patch('/api/v1/admin/users/'+person['id'], json={**{k:disabled[k] for k in ('email','name','role','version')},'active':True}).status_code == 200
        assert member.get('/api/me').status_code == 401
    actions = client.get('/api/v1/admin/access-history').json()['events']
    assert [x['action'] for x in actions] == ['update','update','grant','bootstrap']


def test_admin_does_not_gain_access_to_another_portfolio(admin, services):
    app, client, access = admin
    person = grant(client)
    access.authorize_sso('member-owner', {'email': person['email']})
    _, repo, _ = services
    pid = add_portfolio(repo, owner='member-owner')
    run = repo.submit('member-owner', pid, str(uuid.uuid4()), {'weights': {'CASH':100}})
    for path in (f'/api/v1/portfolios/{pid}',f'/api/v1/runs/{run.id}',f'/api/v1/runs/{run.id}/events',f'/api/v1/runs/{run.id}/artifacts/json'):
        assert client.get(path).status_code == 404


@pytest.mark.parametrize('fields',[{'role':'root'},{'active':'true'},{'email':'invalid'},{'name':''},{'owner':'chosen-owner'},{'permissions':['all']}])
def test_invalid_grants_have_no_partial_user_or_audit(admin, services, fields):
    _, client, _ = admin
    body={'name':'Member','email':'member@example.test', **fields}
    assert client.post('/api/v1/admin/users',json=body).status_code == 422
    assert client.get('/api/v1/admin/users').json()['total'] == 1
    assert client.get('/api/v1/admin/access-history').json()['total'] == 1


def test_duplicate_stale_edit_csrf_and_self_lockout(admin):
    _, client, _ = admin
    person=grant(client)
    assert client.post('/api/v1/admin/users',json={'name':'Duplicate','email':' MEMBER@EXAMPLE.TEST '}).status_code == 409
    body={k:person[k] for k in ('email','name','role','active','version')}
    assert client.patch('/api/v1/admin/users/'+person['id'],json=body,headers={'x-csrf-token':'invalid'}).status_code == 403
    assert client.patch('/api/v1/admin/users/'+person['id'],json=body,headers={'origin':'https://other.example.test'}).status_code == 403
    assert client.patch('/api/v1/admin/users/'+person['id'],json={**body,'name':'Renamed'}).status_code == 200
    assert client.patch('/api/v1/admin/users/'+person['id'],json=body).status_code == 409
    me=next(x for x in client.get('/api/v1/admin/users').json()['users'] if x['id']==client.get('/api/me').json()['id'])
    own={k:me[k] for k in ('email','name','role','active','version')}
    for change in ({'active':False},{'role':'member'}):
        assert client.patch('/api/v1/admin/users/'+me['id'],json={**own,**change}).status_code == 409
    assert client.get('/api/me').json()['role'] == 'admin'


def test_promotion_and_demotion_refresh_existing_sessions(admin):
    app, client, access=admin
    person=grant(client)
    access.authorize_sso('member-owner',{'email':person['email']})
    with signed_in(app,'member-owner') as member:
        body={k:person[k] for k in ('email','name','role','active','version')}
        elevated=client.patch('/api/v1/admin/users/'+person['id'],json={**body,'role':'admin'}).json()
        assert member.get('/api/me').json()['role']=='admin'
        assert member.get('/api/v1/admin/users').status_code==200
        assert client.patch('/api/v1/admin/users/'+person['id'],json={**body,'version':elevated['version']}).status_code==200
        assert member.get('/api/v1/admin/users').status_code==403


def test_unknown_inactive_unverified_and_rebound_sso_denied(admin):
    _, client, access=admin
    with pytest.raises(HTTPException):access.authorize_sso('outsider',{'email':'other@example.test'})
    person=grant(client)
    with pytest.raises(HTTPException):access.authorize_sso('member-owner',{'email':person['email'],'email_verified':False})
    access.authorize_sso('member-owner',{'email':person['email'],'email_verified':True})
    with pytest.raises(HTTPException):access.authorize_sso('different-subject',{'email':person['email'],'email_verified':True})
    grant(client,'disabled@example.test',active=False)
    with pytest.raises(HTTPException):access.authorize_sso('disabled-owner',{'email':'disabled@example.test'})


def test_bootstrap_runs_once_and_never_reinstates_access(admin, services):
    app, client, access=admin
    person=grant(client,'second@example.test',role='admin')
    access.authorize_sso('second-owner',{'email':person['email']})
    me=next(x for x in client.get('/api/v1/admin/users').json()['users'] if x['email']=='admin@example.test')
    with signed_in(app,'second-owner') as second:
        assert second.patch('/api/v1/admin/users/'+me['id'],json={**{k:me[k] for k in ('email','name','role','version')},'active':False}).status_code==200
    access.bootstrap('admin@example.test',owner='admin-owner')
    with pytest.raises(HTTPException):access.active_user('admin-owner')


def test_additive_migration_preserves_existing_portfolios_and_denies_legacy_login(services):
    settings,repo,_=services
    pid=add_portfolio(repo,owner='legacy-owner')
    with repo.session.begin() as db:db.get(Record,'schema').value={'version':1}
    again=Repository(settings.get('DATABASE_URL'));again.initialize();again.initialize()
    assert again.portfolio('legacy-owner',pid).name=='Test portfolio'
    with again.session() as db:assert db.get(Record,'schema').value=={'version':2}
    app=live_app(services)
    token='fixture-old-session'
    with repo.session.begin() as db:db.add(Login(id=hashlib.sha256(token.encode()).hexdigest(),owner='legacy-owner',name='Old user',csrf='fixture',expires=time.time()+3600))
    with TestClient(app) as client:
        client.cookies.set('pa_session',app.state.auth.signer.dumps(token))
        assert client.get('/api/me').status_code==403
        assert client.get('/api/v1/portfolios').status_code==403


def test_selected_existing_account_bootstrap_preserves_owner_and_is_not_first_login(services):
    _,repo,_=services
    pid=add_portfolio(repo,owner='existing-owner')
    run=repo.submit('existing-owner',pid,str(uuid.uuid4()),{})
    access=AccessControl(repo)
    with pytest.raises(RuntimeError):access.bootstrap_from_run(run.id)
    with repo.session.begin() as db:db.add(Login(id='fixture',owner='existing-owner',name='Portfolio owner',csrf='fixture',expires=time.time()+3600))
    access.bootstrap_from_run(run.id)
    assert access.active_user('existing-owner').role=='admin'
    assert access.active_user('existing-owner').email is None
    # The exact already-approved immutable subject can reauthenticate without email.
    assert access.authorize_sso('existing-owner',{'sub':'opaque-subject'}).role=='admin'
    with pytest.raises(HTTPException):access.authorize_sso('first-arrival',{'sub':'another-subject'})
    assert repo.portfolio('existing-owner',pid).owner=='existing-owner'


def test_callback_requires_approved_validated_identity(services):
    app=live_app(services,AUTH_BOOTSTRAP_ADMIN_EMAIL='admin@example.test')
    claims={'iss':ISSUER,'sub':'approved-sub','email':'admin@example.test','email_verified':True}
    async def authorize(request):return {'userinfo':claims}
    app.state.auth.oauth.oracle.authorize_access_token=authorize
    with TestClient(app) as client:
        assert client.get('/auth/callback',follow_redirects=False).status_code==307
        assert client.get('/api/me').json()['role']=='admin'
    claims.update(email='unknown@example.test',sub='unknown-sub')
    with TestClient(app) as denied:
        response=denied.get('/auth/callback',follow_redirects=False)
        assert response.headers['location'].endswith('?access=not-enabled')
        assert denied.get('/api/me').status_code==401
    claims.update(iss='https://untrusted.example.test')
    with TestClient(app) as denied:assert denied.get('/auth/callback').status_code==401


def test_directory_search_and_bound_pagination(admin):
    _,client,_=admin
    grant(client,'analyst@example.test',name='Synthetic Analyst')
    result=client.get('/api/v1/admin/users?q=ANALYST').json()
    assert result['total']==1
    assert 'owner' not in result['users'][0]
    assert client.get('/api/v1/admin/users?offset=-1').status_code==422
    assert client.get('/api/v1/admin/users?q=%25').json()['total']==0


def test_anonymous_cannot_read_or_grant_access(admin):
    app,_,_=admin
    with TestClient(app) as client:
        assert client.get('/api/v1/admin/users').status_code==401
        assert client.get('/api/v1/admin/access-history').status_code==401
        assert client.post('/api/v1/admin/users',json={'email':'nobody@example.test'}).status_code==401


@pytest.mark.parametrize('same_subject', [True, False])
def test_callback_userinfo_email_must_match_validated_subject(services, same_subject):
    app = live_app(services, AUTH_BOOTSTRAP_ADMIN_EMAIL='admin@example.test')
    async def authorize(request):
        return {'userinfo': {'iss': ISSUER, 'sub': 'approved-sub', 'preferred_username': 'opaque-oracle-id'}, 'access_token': 'synthetic'}
    async def userinfo(token):
        return {'sub': 'approved-sub' if same_subject else 'other-sub',
                'email': 'admin@example.test', 'email_verified': True}
    app.state.auth.oauth.oracle.authorize_access_token = authorize
    app.state.auth.oauth.oracle.userinfo = userinfo
    with TestClient(app) as client:
        response = client.get('/auth/callback', follow_redirects=False)
        assert response.status_code == (307 if same_subject else 401)
        assert client.get('/api/me').status_code == (200 if same_subject else 401)
