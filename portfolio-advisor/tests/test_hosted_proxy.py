from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from starlette.responses import Response, RedirectResponse

from portfolio_advisor.api import create_app
from portfolio_advisor.auth import Authentication
from portfolio_advisor.access import AccessControl
from portfolio_advisor.config import Settings


PREFIX = '/20251112/hostedApplications/test/actions/invoke'
ORIGIN = 'https://inference.example.test'


@pytest.fixture
def hosted(services):
    local, repository, store = services
    settings = Settings({**local.values, 'APP_ENV': 'hosted', 'APP_ORIGIN': ORIGIN,
                         'APP_BASE_PATH': PREFIX, 'SSO_ISSUER_URL': 'https://identity.example.test/',
                         'SSO_CLIENT_ID': 'test-client', 'SSO_CLIENT_SECRET': 'test-only-secret'})
    AccessControl(repository).bootstrap('presenter@example.test', owner='owner', name='Presenter')
    return create_app(settings, repository, store), settings, repository


@pytest.mark.parametrize('incoming_prefix', ['', PREFIX])
def test_gateway_and_direct_health_paths_preserve_private_access(hosted, incoming_prefix):
    app, settings, repository = hosted
    with TestClient(app, base_url='http://internal-proxy.example') as client:
        response = client.get(incoming_prefix + '/api/config')
        assert response.json() == {'mode': 'live', 'loginUrl': PREFIX + '/auth/login'}
        assert client.get(incoming_prefix + '/healthz').json()['api'] == 'ok'
        assert client.get(incoming_prefix + '/api/v1/portfolios').status_code == 401
        assert client.get(incoming_prefix + '/api/me').status_code == 401
        assert client.get(incoming_prefix + '/another-application/api/config').status_code == 404
        login = Authentication(settings, repository).issue('owner', 'Presenter', Response())
        assert 'Path=' + PREFIX in login.headers['set-cookie']
        client.headers['cookie'] = login.headers['set-cookie'].split(';')[0]
        csrf = client.get(incoming_prefix + '/api/me').json()['csrf']
        assert client.get(incoming_prefix + '/api/v1/portfolios').json() == []
        response = client.post(incoming_prefix + '/auth/logout', json={},
            headers={'x-csrf-token': csrf, 'origin': 'https://attacker.example'})
        assert response.status_code == 403


def test_gateway_sso_uses_configured_origin_not_forwarded_headers(hosted, monkeypatch):
    from authlib.integrations.starlette_client.apps import StarletteOAuth2App
    captured = {}
    async def redirect(self, request, redirect_uri, **kwargs):
        captured.update(uri=redirect_uri, host=request.url.hostname)
        return RedirectResponse('https://identity.example.test/authorize?' + urlencode({'redirect_uri': redirect_uri}))
    monkeypatch.setattr(StarletteOAuth2App, 'authorize_redirect', redirect)
    with TestClient(hosted[0], base_url='http://internal-proxy.example') as client:
        response = client.get('/auth/login', follow_redirects=False,
            headers={'x-forwarded-host': 'attacker.example', 'x-forwarded-proto': 'http'})
        assert response.status_code == 307
        assert captured == {'uri': ORIGIN + PREFIX + '/auth/callback', 'host': 'inference.example.test'}
