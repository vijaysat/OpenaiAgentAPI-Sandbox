"""Verify the activated hosted image, public UI, health and SSO entry boundary.

This does not log in a user or certify a completed portfolio analysis.
"""
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
from pathlib import Path
import sys
from urllib.parse import parse_qs, urlsplit

import httpx

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP / 'scripts'))
from rollout import Rollout, private_json


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.paths = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script' and attrs.get('src'):
            self.paths.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.paths.append(attrs.get('href', ''))


def verify(r):
    state = r.status()
    if not state.get('verifiedDigest'):
        raise ValueError('The hosted deployment has no verified ACTIVE image identity.')
    base = r.j['applicationURL'].rstrip('/')
    origin = urlsplit(base)
    checks = {}
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.get(base + '/')
        checks['ui'] = response.status_code == 200 and 'text/html' in response.headers.get('content-type', '')
        assets = Assets()
        if checks['ui']:
            assets.feed(response.text)
        checks['assets'] = bool(assets.paths) and all(
            path.startswith(origin.path + '/assets/') and
            client.get(origin.scheme + '://' + origin.netloc + path).status_code == 200
            for path in assets.paths)
        health = client.get(base + '/healthz')
        body = health.json() if health.status_code == 200 else {}
        checks['api_database_worker'] = body.get('api') == 'ok' and body.get('worker') == 'ok'
        config = client.get(base + '/api/config')
        body = config.json() if config.status_code == 200 else {}
        checks['live_configuration'] = body.get('mode') == 'live' and body.get('loginUrl') == origin.path + '/auth/login'
        checks['anonymous_portfolio_denied'] = client.get(base + '/api/v1/portfolios').status_code == 401
        checks['anonymous_identity_denied'] = client.get(base + '/api/me').status_code == 401
        checks['anonymous_user_directory_denied'] = client.get(base + '/api/v1/admin/users').status_code == 401
        checks['anonymous_access_history_denied'] = client.get(base + '/api/v1/admin/access-history').status_code == 401
        login = client.get(base + '/auth/login')
        target = urlsplit(login.headers.get('location', ''))
        params = parse_qs(target.query)
        checks['sso_redirect'] = (login.status_code in (302, 303, 307) and target.scheme == 'https'
            and target.hostname == urlsplit(r.j['sso']['domain']).hostname
            and params.get('client_id') == [r.j['sso']['clientId']]
            and params.get('redirect_uri') == [base + '/auth/callback']
            and params.get('code_challenge_method') == ['S256']
            and bool(params.get('state')) and bool(params.get('nonce')))
        checks['sso_profile_scopes'] = {'openid', 'email', 'profile'}.issubset(set(params.get('scope', [''])[0].split()))
    result = {'checkedAt': datetime.now(timezone.utc).isoformat(), 'applicationURL': base + '/',
              'digest': state['verifiedDigest'], 'checks': checks, 'passed': all(checks.values()),
              'browserLoginVerified': False, 'liveAnalysisVerified': False}
    private_json(r.path.parent / 'hosted-smoke.json', result)
    return result


if __name__ == '__main__':
    try:
        result = verify(Rollout(APP / 'deploy/private/vault-bootstrap.json', 'Sandbox'))
        print(json.dumps(result, indent=2))
        raise SystemExit(0 if result['passed'] else 1)
    except Exception as exc:
        raise SystemExit('Hosted verification failed (' + type(exc).__name__ +
                         '); inspect deployment state and redacted service messages.') from None
