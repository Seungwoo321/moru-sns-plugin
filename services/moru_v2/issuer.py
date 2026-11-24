"""OAuth resource-server integration using PyJWT, never a token issuer.

Metadata must be fetched by the host from its explicitly configured HTTPS issuer
(no URLs from tool arguments). JWKS URLs are pinned; token jku/x5u are never used.
"""
import asyncio
from urllib.parse import urlsplit
import jwt
from mcp.server.auth.provider import AccessToken


def https(value):
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or
            parsed.query or parsed.fragment or '*' in value):
        raise ValueError('Exact HTTPS OAuth endpoint required')
    return value


class IssuerVerifier:
    def __init__(self, *, issuer, resource, metadata, clients, scopes, algorithms=('EdDSA',)):
        self.issuer, self.resource = https(issuer), https(resource)
        self.clients, self.scopes = frozenset(clients), frozenset(scopes)
        self.algorithms = tuple(algorithms)
        if (not self.clients or not self.scopes or not self.algorithms or
                set(self.algorithms) - {'EdDSA', 'RS256', 'ES256'}):
            raise ValueError('Explicit client/scope/asymmetric algorithm policy required')
        if metadata.get('issuer') != issuer or 'S256' not in metadata.get('code_challenge_methods_supported', []):
            raise ValueError('Issuer metadata or PKCE S256 unavailable')
        if 'code' not in metadata.get('response_types_supported', []):
            raise ValueError('Authorization-code flow unavailable')
        if not self.scopes.issubset(metadata.get('scopes_supported', [])):
            raise ValueError('Required scopes not supported by issuer')
        if not metadata.get('token_endpoint_auth_methods_supported'):
            raise ValueError('Token endpoint authentication metadata required')
        for field in ('authorization_endpoint', 'token_endpoint', 'jwks_uri'):
            https(metadata[field])
        self.jwks = jwt.PyJWKClient(metadata['jwks_uri'], timeout=5, lifespan=300)

    async def verify_token(self, token):
        try:
            key = await asyncio.to_thread(self.jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(token, key.key, algorithms=self.algorithms,
                issuer=self.issuer, audience=self.resource,
                options={'require': ['iss', 'aud', 'exp', 'sub', 'client_id']})
            client, subject, scope = claims['client_id'], claims['sub'], claims.get('scope', '')
            if (client not in self.clients or not isinstance(subject, str) or not subject or
                    not isinstance(scope, str) or not self.scopes.issubset(scope.split())):
                return None
            return AccessToken(token=token, client_id=client, subject=subject,
                resource=self.resource, expires_at=claims['exp'], scopes=scope.split())
        except Exception:
            return None
