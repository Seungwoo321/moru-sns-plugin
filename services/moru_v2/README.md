# Moru v2 account connections — first implementation slice

This service is separate from the released v1 0.1.6 plugin. It imports no v1
engine, ledger, environment credentials or vault. Nothing here deploys, registers
an endpoint, schedules work or publishes content. X is rejected at both HTTP and
schema boundaries; a future provider must explicitly extend both.

## Implemented boundary

`app.create_app` serves `GET /connections`, `POST /connections/{platform}/start`,
`GET /connections/{platform}/callback` and `POST /connections/{platform}/disconnect`.
The host must inject a trusted browser-session verifier and platform adapters.
No owner, account, secret, scope or redirect URI is taken from a request body.
The verified `(issuer, subject)` is the user identity; a verified session ID binds
the OAuth attempt to the initiating browser session. The host must use Secure,
HttpOnly, SameSite=Lax session cookies and verify the live session on every request.
POSTs require the exact service Origin. Callback state is random, stored only as
a hash, expires after ten minutes, and is consumed once atomically before exchange.
A cancelled/failed exchange needs a new authorization attempt.

The Postgres row is keyed by owner/platform. Atomic generation checks stop a late
callback from resurrecting an account after disconnect, or overwriting a newer
attempt. Connecting an already connected account requires explicit disconnect.
Tokens use AES-256-GCM with fresh nonces and owner/platform/account/generation
binding. Key IDs support reads during key rotation; keys belong in separate v2
server secrets, never in Neon rows or the v1 vault. Rotation/re-encryption tooling
and retention policies for database backups are future operational work.

Disconnect immediately clears local credentials and invalidates pending attempts.
It returns `provider_revocation: not_attempted`: Meta-side permission revocation is
not claimed by this slice. Existing encrypted database backups are not erased by
an UPDATE. No code exposes token plaintext through HTTP or MCP.

`mcp_app.create_server` uses the maintained MCP SDK's OAuth resource-server
middleware and discovery. Its required verifier must validate signature/issuer
or use secure introspection; the wrapper additionally requires audience/resource,
expiry, approved OAuth client and subject. `moru_connections` derives its owner
from that verified subject, never a tool argument. MCP grants and Meta grants are
separate. Only `moru:connections:read` exists; there are no publish tools.

## Vercel / Neon integration boundary

Vercel is the intended ASGI host. A deployment entry point is deliberately not
created before the actual HTTPS service origin, v2 secret bindings and issuer are
selected and verified. The service performs no migrations on startup. Use the
versioned SQL migration on the dedicated project's development branch first.
Managed Neon Auth is the default planned Moru identity/OAuth issuer; its hosted
login/consent UI and verified session adapter are not implemented in this slice.
The browser session issuer and MCP issuer must be the same canonical value so
identities map consistently. An access token is not a browser session.

Neon inventory checked 2026-11-24 through the existing connection: both available
organizations were Free, with 3 and 2 existing projects; neither contained moru-v2.
Created only the new `moru-v2` project `autumn-block-39202450` in the console-managed
Free organization, Singapore (`aws-ap-southeast-1`), PostgreSQL 17, fixed 0.25 CU.
A custom suspend interval was rejected without creating a project; creation with
the provider's defaults succeeded. Do not interpret the API's default interval
value `0` as evidence of paid always-on compute. [Free-plan limits](https://neon.com/blog/neon-free-plan-1-gb-per-project)
include 100 projects and 100 CU-hours/project/month, with no plan upgrade requested.

- New main branch: `br-spring-paper-biflgfs4`, left without application schema.
- New development branch: `br-solitary-voice-bi7heybc`, `dev-account-connections`.
- Migration 001 applied only to that development branch, containing synthetic
  state-transition smoke data; no real identities or tokens were copied.
- Neon connector SQL verified PostgreSQL 17.12, schema creation, tenant filtering,
  one-time consumption and generation invalidation.
- Direct psycopg integration from this managed environment failed at TCP 5432
  (connection closed / IPv6 unreachable), before any application query. This is
  not a passing end-to-end Neon test. CI runs the complete suite on PostgreSQL 17.

## Verification

```sh
python -m venv .venv
.venv/bin/pip install -r services/moru_v2/requirements.txt pytest==9.2.0
.venv/bin/python -m pytest services/moru_v2/tests -q
# Optional: point MORU_V2_TEST_DATABASE_URL ONLY at an isolated development DB.
# Tests create schema and synthetic rows. Never use production or another product.
```

The suite drives actual account-management HTTP routes and MCP HTTP transport:
user/session/platform isolation, cancellation, expiry, replay, concurrent state
consumption, disconnect during exchange, replacement attempts, encryption binding,
CSRF, wrong Host, missing scopes and invalid resource/client/expiry. Providers and
browser/MCP credential verifiers are explicit test doubles, not production auth.

## Official Meta connection adapter

`meta.OfficialMetaProvider` now implements server-side authorization-code exchange,
long-lived-token exchange and official `/me` identity verification for Threads and
Instagram Login. It requests only `threads_basic` or `instagram_business_basic`;
publishing, metrics, messages and comments are not requested. Token expiry comes
from the provider response. Instagram's wrapped single-token response is supported;
ambiguous grants, missing basic permission, unexpected token types, account mismatch
and redirects fail closed. The code exchange is never automatically retried.

The callback is configured on the trusted host, never supplied by a browser request.
The adapter remains unbound to live credentials. Instantiating it does not register
an app, add permissions or change callback allowlists. Tests drive the HTTP callback
through this adapter using a mock HTTP transport, including a rejected state making
zero provider calls, correct encrypted storage and replay making no extra requests.

Follow the official [Threads authorization](https://developers.facebook.com/documentation/threads/get-started/get-access-tokens-and-permissions),
[Threads long-lived tokens](https://developers.facebook.com/documentation/threads/get-started/long-lived-tokens)
and [Instagram Business Login](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-instagram-login/business-login)
contracts (checked 2026-11-24). Meta requires secret query parameters for long-token
exchange: disable HTTP client/proxy request logging and tracing at the hosting
boundary. Adapter exceptions never return upstream URLs/bodies. This is a required
host gate, not a claim that arbitrary external telemetry is automatically redacted.

## Remaining gates

1. Implement explicit remote permission revocation and refresh/key rotation policy.
   Unified Login is not silently substituted for Instagram Login; v1 is unchanged.
2. Implement Moru login/consent, live browser sessions, OAuth issuer/client binding
   and the Vercel entry point. Verify logout, PKCE, consent denial and token expiry.
3. Obtain approval before any Meta app/callback/permission change, paid resource
   or public deployment. Use new v2 secret bindings; do not read/copy v1 credentials.
4. Review public-account Advanced Access, privacy/deletion requirements, retention,
   rate limits, audit logging/redaction and remote revocation before public use.
5. Resolve the plugin importer/schema discrepancy documented in the existing
   `plugins/moru-sns/remote/WORK-PACKAGING.md`, then verify a real installed Work
   connection. No active plugin manifest is changed by this slice.
