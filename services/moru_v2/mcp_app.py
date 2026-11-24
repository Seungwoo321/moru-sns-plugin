"""Public-user MCP boundary. MCP OAuth credentials never become Meta credentials."""
import time
from urllib.parse import urlsplit
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl
from .domain import digest


class ResourceVerifier:
    def __init__(self, delegate, resource, clients):
        if delegate is None or not clients:
            raise ValueError('Trusted signature/introspection verifier and OAuth clients required')
        self.delegate, self.resource, self.clients = delegate, resource, frozenset(clients)

    async def verify_token(self, token):
        try:
            result = await self.delegate.verify_token(token)
            if (result is None or not result.subject or result.resource != self.resource or
                    result.client_id not in self.clients or result.expires_at is None or
                    result.expires_at <= time.time()):
                return None
            return result
        except Exception:
            return None


def create_server(store, *, verifier, issuer, resource, clients, origins):
    for value in (issuer, resource, *origins):
        url = urlsplit(value)
        if (url.scheme != 'https' or not url.hostname or url.username or url.password or
                url.query or url.fragment or '*' in value):
            raise ValueError('Exact HTTPS OAuth configuration required')
    if (urlsplit(resource).path != '/mcp' or not origins or
            any(urlsplit(o).path not in ('', '/') for o in origins)):
        raise ValueError('Explicit origins and /mcp resource required')
    server = FastMCP('Moru v2', stateless_http=True, json_response=True, log_level='CRITICAL',
        token_verifier=ResourceVerifier(verifier, resource, clients),
        auth=AuthSettings(issuer_url=AnyHttpUrl(issuer), resource_server_url=AnyHttpUrl(resource),
                          required_scopes=['moru:connections:read']),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=[urlsplit(resource).netloc], allowed_origins=origins))

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False,
                                            openWorldHint=False))
    async def moru_connections() -> dict:
        """List only the signed-in user's connection status. No tokens or publish capability."""
        token = get_access_token()
        if token is None or 'moru:connections:read' not in token.scopes:
            return {'error': 'insufficient_scope'}
        try:
            return {'connections': store.list(digest(issuer, token.subject))}
        except Exception:
            return {'error': 'connection_service_unavailable'}
    return server
