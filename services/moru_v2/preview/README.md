# Protected preview preparation (not deployed)

This is a synthetic-only diagnostic artifact, not the Moru account application.
No real login, MCP service, Neon credentials, Meta credentials, token exchange,
publishing or database mutation is included. It reuses the service's callback
query scrubber and scoped HTTP log redaction. Every endpoint refuses non-Preview
`VERCEL_ENV` values with 503. This runtime guard does not replace Vercel protection.

Prepare a new upload directory, without connecting to Vercel:

```sh
python services/moru_v2/preview/prepare.py /tmp/moru-preview-source
```

The exact allowlist is `api/index.py`, `privacy.py`, `requirements.txt`,
`vercel.json` plus a SHA-256 manifest. It never copies the repository, v1 package,
.env files, credentials or operating data. Git automatic deployment is disabled
in the artifact. No deploy command or CI deployment workflow is installed.
The prepared source's ASGI entrypoint and routes are tested locally; an actual
Vercel build, route rewrite and runtime are not yet verified.

## Recorded platform state — 2026-11-24

- Team: `seungwoo321s-projects` (`team_wP7EhT7i1qUv4LIPnKnL5oRM`).
- Plan returned by deployment-context API: **Pro**, not Hobby. No plan/billing
  changes were made. Included build/compute headroom was not returned by this
  API, so zero incremental usage cost is not claimed.
- Searching this team for `moru` returned no projects before creation.
- Created `moru-v2` (`prj_h8SI4plelW0aLJVguYDLPdopEkCj`) without a Git repository.
- Creation and subsequent GET both returned SSO enabled, `deploymentType: all`,
  password protection disabled, no domains, `live: false`, no latest deployment.
- Deployment list returned zero records. No source or environment variables were
  uploaded. No share link, protection exception, password protection or bypass
  rule was created. No Neon, Meta, v1 or other product setting was changed.

Vercel Authentication is available on all plans without the paid password
protection add-on. However, configuration evidence alone is **not** an anonymous
access test. There is no deployment URL to test yet.

## Exact execution blockers

Vercel documents that a new project's **first deployment is production even when
preview is requested**. That deployment uses Production variables. The current
request permits a protected development Preview, so we did not silently create a
production bootstrap (including an intentionally failed or canceled deployment).
Before executing, resolve this first-deployment scope explicitly and verify that
the existing Pro team's included usage permits the build without paid overage.
A protected production target is not necessarily public, but it is still not the
requested Preview target. No existing product deployment may be reused to bypass
this constraint.

## Verification sequence after the blockers are resolved

1. Re-read the exact project, all-deployments SSO, Git linkage, deployment history,
   and cost conditions. Never disable protection or create exceptions/share links.
2. Use only the reviewed allowlisted artifact. Inspect the returned actual target
   and project ID; a requested preview target is not sufficient evidence. Do not
   attach domains or promote to production.
3. With no cookies or authorization/bypass headers and redirects disabled, request
   `/health` once. Record status and whether Vercel intercepted it. The expected
   result is the platform authentication gate; the app's synthetic JSON must not
   be reachable anonymously. Distinguish platform rejection from app-generated
   401/403/503. Stop if the app is anonymously reachable.
4. Through an authorized team-member browser session (no share link or protection
   exception), verify `/health`. Then visit each callback path with synthetic
   query values only, for example
   `/auth/callback?code=MORU_CANARY_CODE_01&state=MORU_CANARY_STATE_01&mode=ok`.
   Repeat with `mode=error`, and for the Threads/Instagram callback routes.
5. Confirm fixed success/error responses, no reflected query, no-store and
   no-referrer. Correlate the fixed `synthetic_callback_completed` / `_failed`
   log events with request time and deployment ID. Search raw platform access
   logs, runtime logs, errors and any configured exports for both canary values.
   Do not consider an empty log result a pass unless ingestion and the executed
   request are independently confirmed. Do not add a paid drain/observability
   feature. Any unavailable log surface remains an explicit release gate.
6. Only after logging protection is proven may a separate reviewed integration
   introduce the dedicated Neon development branch. No production/main branch,
   real Meta account or v1 vault is allowed. This probe itself needs no database.

Protected developer-browser access says nothing about whether an external MCP
client can reach discovery, authorization or callbacks through Vercel SSO. Keep
that interoperability assessment separate; never relax protection to make it pass.

References checked 2026-11-24:
- [Deployment protection](https://vercel.com/docs/deployment-protection)
- [First deployment and environments](https://vercel.com/docs/deployments/environments)
- [Vercel Authentication](https://vercel.com/docs/deployment-protection/methods-to-protect-deployments/vercel-authentication)
- [Disable Git automatic deployment](https://vercel.com/docs/project-configuration/git-configuration)
- [Python ASGI runtime](https://vercel.com/docs/functions/runtimes/python)
