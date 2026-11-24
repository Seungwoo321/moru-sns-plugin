"""Trust-boundary contracts. Only verified service identity may supply the owner."""
from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Protocol

PLATFORMS = frozenset({'threads', 'instagram'})


class Blocked(Exception):
    pass


def digest(*parts: str) -> str:
    return sha256(json.dumps(parts, separators=(',', ':')).encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str
    session_id: str

    @property
    def owner(self):
        if not self.issuer or not self.subject or not self.session_id:
            raise Blocked('unauthenticated')
        return digest(self.issuer, self.subject)


@dataclass(frozen=True, repr=False)
class Grant:
    account_id: str
    access_token: str
    expires_at: int
    scopes: tuple[str, ...]


class MetaProvider(Protocol):
    """Server-configured adapter, never accepts a host/redirect/scope from a caller.

    exchange must verify identity through the selected platform's official /me,
    granted scopes and token expiry before returning. No publication methods.
    A production adapter must not log code, token, HTTP URLs or response bodies.
    """
    def authorize_url(self, state: str) -> str: ...
    async def exchange(self, code: str) -> Grant: ...
