"""Authenticated MCP app factory. No listener, deployment, OAuth issuer or scheduler."""
import asyncio
import time
from typing import Literal
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.auth.routes import create_protected_resource_routes
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl

from engine import Blocked

Platform = Literal['threads', 'instagram']


class BoundVerifier:
    """Require a trusted verifier, exact resource, expiry, client and owner binding."""
    def __init__(self, delegate, resource, clients, subjects):
        if delegate is None or not clients or not subjects:
            raise ValueError('A verified issuer and explicit clients/owners are required.')
        self.delegate, self.resource = delegate, resource
        self.clients, self.subjects = frozenset(clients), frozenset(subjects)

    async def verify_token(self, token):
        try:
            verified = await self.delegate.verify_token(token)
            if (verified is None or verified.resource != self.resource or
                    verified.client_id not in self.clients or verified.subject not in self.subjects or
                    verified.expires_at is None or verified.expires_at <= time.time()):
                return None
            return verified
        except Exception:
            return None


def create_server(engine, *, verifier: TokenVerifier, issuer_url: str, resource_url: str,
                  allowed_clients: set[str], allowed_subjects: set[str], allowed_origins: list[str]):
    """Host supplies an existing OAuth verifier; never accepts Meta tokens as MCP auth."""
    for value in (issuer_url, resource_url, *allowed_origins):
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
                parsed.query or parsed.fragment or '*' in value):
            raise ValueError('Exact HTTPS issuer/resource/origin URLs are required.')
    if urlsplit(resource_url).path != '/mcp':
        raise ValueError('The resource URL must identify the /mcp endpoint.')
    bound = BoundVerifier(verifier, resource_url, allowed_clients, allowed_subjects)
    server = FastMCP(
        'Moru SNS', stateless_http=True, json_response=True, log_level='CRITICAL',
        max_request_body_size=32768, token_verifier=bound,
        auth=AuthSettings(issuer_url=AnyHttpUrl(issuer_url), resource_server_url=AnyHttpUrl(resource_url),
                          required_scopes=['moru:read']),
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
            allowed_hosts=[urlsplit(resource_url).netloc], allowed_origins=allowed_origins),
        instructions='Work owns scheduling, approved Slack source review and editorial decisions. '
                     'Use stable job IDs and the same content hash on resume. Never bypass missing state. '
                     'Only Threads and Instagram are enabled. Plan is not public-content authorization.')

    async def call(operation, platform, **kwargs):
        token = get_access_token()
        scope = 'moru:publish' if operation == 'publish' else 'moru:read'
        if token is None or scope not in token.scopes:
            return {'status': 'blocked', 'reason': 'insufficient_scope'}
        try:
            return await asyncio.to_thread(engine.call, operation, platform, **kwargs)
        except Blocked as error:
            return {'status': 'blocked', 'reason': str(error)}
        except Exception:
            return {'status': 'blocked', 'reason': 'internal_error_no_automatic_retry'}

    read = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

    @server.tool(annotations=read)
    async def moru_status(platform: Platform) -> dict:
        """Check injected key configuration only; never claims account/schedule readiness."""
        return await call('status', platform)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
    async def moru_check(platform: Platform) -> dict:
        """Read the fixed account through the official API after validating its existing ledger."""
        return await call('check', platform)

    @server.tool(annotations=read)
    async def moru_plan(platform: Platform, job_id: str, text: str, media_id: str | None = None) -> dict:
        """Validate final text/registered Reel and return its content hash; no publication permission."""
        return await call('plan', platform, job_id=job_id, text=text, media_id=media_id)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False,
                                           idempotentHint=True, openWorldHint=True))
    async def moru_publish(platform: Platform, job_id: str, text: str, reviewed_hash: str,
                           media_id: str | None = None) -> dict:
        """Publish only Work-reviewed, user-authorized content. Requires publish scope and server enablement.

        Keep the same job ID, text, media and hash on resume. Existing ledger and six-hour minimum apply.
        The reviewed hash detects changed content; it does not grant public-content authorization.
        """
        return await call('publish', platform, job_id=job_id, text=text, media_id=media_id,
                          reviewed_hash=reviewed_hash)

    @server.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
    async def moru_metrics(platform: Platform, job_id: str) -> dict:
        """Read metrics only for a saved successful job in the fixed account's existing ledger."""
        return await call('metrics', platform, job_id=job_id)

    return server


def create_app(server):
    """Expose both scopes in discovery while keeping read calls read-only authorized."""
    app = server.streamable_http_app()
    settings = server.settings.auth
    metadata = create_protected_resource_routes(
        resource_url=settings.resource_server_url, authorization_servers=[settings.issuer_url],
        scopes_supported=['moru:read', 'moru:publish'], resource_name='Moru SNS')
    paths = {route.path for route in metadata}
    app.routes[:] = [route for route in app.routes if route.path not in paths] + metadata
    return app
