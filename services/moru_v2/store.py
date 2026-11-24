"""Atomic connection lifecycle on Postgres; SQLite is only an offline test adapter."""
import json
import secrets
import time
from sqlalchemy import (MetaData, Table, Column, String, BigInteger, LargeBinary,
                        CheckConstraint, select, update)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .domain import Blocked, Grant, PLATFORMS, digest

metadata = MetaData()
connections = Table('moru_v2_connections', metadata,
    Column('owner', String(64), primary_key=True),
    Column('platform', String(16), primary_key=True),
    Column('generation', String(64), nullable=False),
    Column('status', String(16), nullable=False),
    Column('state_hash', String(64)), Column('binding_hash', String(64)),
    Column('state_expires', BigInteger), Column('account_id', String(128)),
    Column('ciphertext', LargeBinary), Column('key_id', String(64)),
    Column('expires_at', BigInteger), Column('scopes', String(2048)),
    CheckConstraint("platform IN ('threads', 'instagram')"),
    CheckConstraint("status IN ('pending', 'exchanging', 'connected', 'disconnected')"),
    CheckConstraint("status != 'connected' OR (ciphertext IS NOT NULL AND key_id IS NOT NULL "
                    "AND account_id IS NOT NULL AND expires_at IS NOT NULL)"))


class Vault:
    def __init__(self, keys: dict[str, bytes], active_key: str):
        if active_key not in keys or any(len(k) != 32 for k in keys.values()):
            raise ValueError('Explicit 256-bit encryption keys required')
        self.keys, self.active_key = dict(keys), active_key

    def seal(self, token, owner, platform, account, generation):
        nonce = secrets.token_bytes(12)
        aad = digest(owner, platform, account, generation).encode()
        return nonce + AESGCM(self.keys[self.active_key]).encrypt(nonce, token.encode(), aad)

    def open(self, row):
        aad = digest(row['owner'], row['platform'], row['account_id'], row['generation']).encode()
        value = row['ciphertext']
        return AESGCM(self.keys[row['key_id']]).decrypt(value[:12], value[12:], aad).decode()


class Store:
    def __init__(self, engine, vault: Vault, clock=time.time):
        if engine.dialect.name not in ('postgresql', 'sqlite'):
            raise ValueError('Unsupported database')
        self.engine, self.vault, self.clock = engine, vault, clock

    @staticmethod
    def scope(owner, platform):
        if platform not in PLATFORMS:
            raise Blocked('unsupported_platform')
        if not owner:
            raise Blocked('unauthenticated')
        return (connections.c.owner == owner) & (connections.c.platform == platform)

    def begin(self, principal, platform):
        owner = principal.owner
        where = self.scope(owner, platform)
        state, generation = secrets.token_urlsafe(32), secrets.token_hex(32)
        values = dict(generation=generation, status='pending', state_hash=digest(state),
                      binding_hash=digest(principal.session_id), state_expires=int(self.clock()) + 600,
                      account_id=None, ciphertext=None, key_id=None, expires_at=None, scopes=None)
        insert = pg_insert if self.engine.dialect.name == 'postgresql' else sqlite_insert
        with self.engine.begin() as db:
            db.execute(insert(connections).values(owner=owner, platform=platform, **values)
                       .on_conflict_do_nothing(index_elements=['owner', 'platform']))
            result = db.execute(update(connections).where(where, connections.c.status != 'connected')
                                .values(**values))
            if result.rowcount != 1:
                raise Blocked('disconnect_before_reconnecting')
        return state

    def consume(self, principal, platform, state):
        where = self.scope(principal.owner, platform)
        with self.engine.begin() as db:
            generation = db.execute(update(connections).where(
                where, connections.c.status == 'pending', connections.c.state_hash == digest(state),
                connections.c.binding_hash == digest(principal.session_id),
                connections.c.state_expires > int(self.clock()))
                .values(status='exchanging', state_hash=None, binding_hash=None, state_expires=None)
                .returning(connections.c.generation)).scalar_one_or_none()
        if generation is None:
            raise Blocked('invalid_or_expired_state')
        return generation

    def finish(self, owner, platform, generation, grant: Grant):
        if (not grant.account_id or len(grant.account_id) > 128 or not grant.access_token or
                grant.expires_at <= self.clock() or not grant.scopes):
            raise Blocked('invalid_grant')
        encrypted = self.vault.seal(grant.access_token, owner, platform, grant.account_id, generation)
        with self.engine.begin() as db:
            result = db.execute(update(connections).where(self.scope(owner, platform),
                connections.c.generation == generation, connections.c.status == 'exchanging')
                .values(status='connected', account_id=grant.account_id, ciphertext=encrypted,
                        key_id=self.vault.active_key, expires_at=grant.expires_at,
                        scopes=json.dumps(grant.scopes)))
        if result.rowcount != 1:
            raise Blocked('connection_cancelled')

    def fail(self, owner, platform, generation):
        with self.engine.begin() as db:
            db.execute(update(connections).where(self.scope(owner, platform),
                connections.c.generation == generation, connections.c.status == 'exchanging')
                .values(status='disconnected'))

    def disconnect(self, owner, platform):
        with self.engine.begin() as db:
            db.execute(update(connections).where(self.scope(owner, platform)).values(
                status='disconnected', generation=secrets.token_hex(32), state_hash=None,
                binding_hash=None, state_expires=None, account_id=None, ciphertext=None,
                key_id=None, expires_at=None, scopes=None))
        # Explicitly local: no false claim that Meta's grant has been revoked.
        return {'status': 'disconnected', 'provider_revocation': 'not_attempted'}

    def list(self, owner):
        with self.engine.connect() as db:
            rows = db.execute(select(connections.c.platform, connections.c.status,
                connections.c.account_id, connections.c.expires_at).where(connections.c.owner == owner))
            return [dict(row._mapping, status=('expired' if row.status == 'connected' and
                    row.expires_at <= self.clock() else row.status)) for row in rows]
