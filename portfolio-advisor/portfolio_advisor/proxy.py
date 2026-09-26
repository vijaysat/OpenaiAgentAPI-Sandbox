"""Adapt OCI's stripped path and internal Host to our configured public origin."""
from urllib.parse import urlsplit


class OCIGatewayMiddleware:
    def __init__(self, app, *, origin, prefix):
        self.app, self.prefix = app, prefix
        self.host = urlsplit(origin).netloc.encode('ascii')

    async def __call__(self, scope, receive, send):
        if scope['type'] in ('http', 'websocket'):
            scope = dict(scope)
            # Hosted containers are reached through OCI's gateway or the local
            # health probe. OCI rewrites Host and strips the invoke prefix.
            # Use deployment configuration, never client-supplied forwarded
            # headers, for redirects and routing. Authentication/CSRF still run.
            scope['headers'] = [(k, v) for k, v in scope['headers'] if k.lower() != b'host']
            scope['headers'].append((b'host', self.host))
            scope['scheme'] = 'https'
            path = scope['path']
            if self.prefix and path != self.prefix and not path.startswith(self.prefix + '/'):
                scope['path'] = self.prefix + path
                if 'raw_path' in scope:
                    scope['raw_path'] = self.prefix.encode('ascii') + scope['raw_path']
        await self.app(scope, receive, send)
