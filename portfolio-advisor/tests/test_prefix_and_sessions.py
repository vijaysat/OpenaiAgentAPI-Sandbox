from fastapi.testclient import TestClient
from portfolio_advisor.api import create_app
from portfolio_advisor.db import Login
from sqlalchemy import select


def test_url_prefix_and_logout(services):
    settings,repo,store=services
    settings.values['APP_BASE_PATH']='/20251112/hostedApplications/test/actions/invoke'
    prefix=settings.base_path
    with TestClient(create_app(settings,repo,store)) as c:
        assert c.get('/api/config').status_code==404
        assert c.get(prefix+'/api/config').json()['loginUrl']==prefix+'/auth/login'
        c.get(prefix+'/auth/login')
        csrf=c.get(prefix+'/api/me').json()['csrf']
        response=c.post(prefix+'/auth/logout',json={},headers={'x-csrf-token':csrf})
        assert response.status_code==200
        assert c.get(prefix+'/api/me').status_code==401


def test_expired_and_tampered_cookie(client,services):
    _,repo,_=services
    with repo.session.begin() as db:
        for login in db.scalars(select(Login)):login.expires=0
    assert client.get('/api/me').status_code==401
    client.cookies.clear();client.cookies.set('pa_session','forged')
    assert client.get('/api/me').status_code==401
