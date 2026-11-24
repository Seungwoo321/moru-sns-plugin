# Moru v2 account connections — isolated implementation

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
Managed Neon Auth is the default planned Moru identity/OAuth issuer. The post-login
continuation, consent and connection UI is implemented below; initial login and
the verified server session adapter remain unconfigured.
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
exchange: task-local redaction now removes HTTPX/httpcore log messages and exception
details only during Meta exchanges. Unrelated requests and application logs remain
visible. CallbackPrivacyMiddleware removes the full callback query from the shared
ASGI scope before downstream processing; the handler reads it only from task-local
context. Canary tests cover INFO, warning, exception and concurrent-request paths.
Reverse proxies, Vercel access logs and external tracing can observe the URL before
ASGI entry: verify their query/body/header capture is disabled before any live login
or deployment. In-process protection is not evidence those external logs are safe.

## Remaining gates

1. Implement explicit remote permission revocation and refresh/key rotation policy.
   Unified Login is not silently substituted for Instagram Login; v1 is unchanged.
2. Complete initial Moru login/logout, live server sessions, OAuth issuer/client
   binding and the Vercel entry point. Verify the existing UI with live PKCE,
   consent denial and token expiry.
3. Obtain approval before any Meta app/callback/permission change, paid resource
   or public deployment. Use new v2 secret bindings; do not read/copy v1 credentials.
4. Review public-account Advanced Access, privacy/deletion requirements, retention,
   rate limits, audit logging/redaction and remote revocation before public use.
5. Resolve the plugin importer/schema discrepancy documented in the existing
   `plugins/moru-sns/remote/WORK-PACKAGING.md`, then verify a real installed Work
   connection. No active plugin manifest is changed by this slice.

## Login continuation, consent and account UI

The next isolated slice adds `/auth/sign-in` (already-signed-in continuation),
`/auth/consent` and `/account`. Build the local bundle with:

```sh
npm ci --prefix services/moru_v2/web
npm test --prefix services/moru_v2/web
npm run build --prefix services/moru_v2/web
```

`@neon/auth` is pinned to 0.8.2 in the lockfile. Its documented `getSession`,
`oauth2.continue`, `oauth2.publicClient` and `oauth2.consent` calls own signed-query
handling and navigation. Moru does not issue codes/tokens, implement PKCE, invent
registration endpoints, or reconstruct OAuth continuation URLs. Consent presents
the client name, callback hosts, requested scope/resource and loopback warning;
all variable display values use text nodes. Allow/deny requires a fresh same-user
session check. Account actions carry a session-bound context returned by the
server; a stale page cannot operate after switching users or sessions. Account
metadata is never embedded in the public HTML shell. Callback HTML uses fixed
messages and a fixed `/account` retry link, with no provider error reflection.

`identity_ready=False` is the default: the UI performs no SDK/network actions and
explicitly says real login is unavailable. Set it true only after implementing
and verifying the host's live cookie/session bridge to the same branch issuer.
This PR does **not** implement password collection, initial login, logout or that
server session bridge. An existing signed-in session can resume via the SDK; an
absent session fails closed. Do not confuse browser SDK session checks with server
API authorization: every data request still runs the trusted server verifier.
CSP prohibits framing and third-party scripts; only the configured issuer origin
is added to connect-src. Production session cookies and cross-origin SDK behavior
remain unverified until the actual host/issuer are configured.

`issuer.IssuerVerifier` is a resource-server adapter using PyJWT/JWKS. The host must
fetch issuer metadata from an explicitly configured HTTPS discovery URL, without
following caller-supplied locations, and pass the verified document. Construction
rejects an issuer mismatch, missing PKCE S256, missing authorization-code metadata,
unsupported requested scopes or absent token-auth-method metadata. JWT verification
requires an allowed asymmetric signature, exact issuer, audience membership,
expiration, subject, client and scopes. A JWT cannot choose its own JWKS URL.
Metadata preflight is not evidence of a live authorization-code/PKCE exchange.

The existing MCP SDK still serves resource metadata and authentication challenges.
`create_server(read_scope=...)` makes scope policy explicit; its default remains
`moru:connections:read`. Managed Neon currently documents only OIDC scopes, so
that default deliberately fails preflight with Neon. An operator must explicitly
select and verify a supported read scope (for example `profile`) consistently in
the verifier/server/plugin before any connection. Do not silently broaden scopes,
enable DCR, fabricate a registered client, or accept arbitrary CIMD clients. Copy
actual client/callback identifiers from the connection management page and keep
the exact branch issuer/resource. Registration and PKCE enforcement belong to the
managed authorization server and OpenAI client, not this resource-server adapter.

References checked 2026-11-24: [OpenAI OAuth/MCP requirements](https://developers.openai.com/plugins/build/auth),
[Neon OAuth provider and UI SDK](https://neon.com/docs/auth/guides/plugins/oauth-provider).
Tests use real DOM event handlers with a fake SDK, actual HTTP/ASGI routes, real
Ed25519 signatures and test JWKS keys. No live issuer, browser-to-provider login,
PKCE token exchange, registered OpenAI connection or Vercel log behavior is claimed.
The dedicated Neon development branch and v1 resources are unchanged by this slice.
