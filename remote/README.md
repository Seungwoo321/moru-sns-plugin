# Remote MCP transport proposal

This is an optional transport **under development**, not an installed Work web connection. It uses the existing publisher engine. It does not create accounts, tokens, a database, a scheduler, a cloud service, a public endpoint, or a plugin separate from Moru SNS. Do not run a branch build as the production operator.

## Supported official route

ChatGPT Work web can call authenticated remote MCP tools bundled in a plugin. Local Codex CLI configuration is not a Work web connection. The Python MCP SDK implements Streamable HTTP, OAuth resource-server middleware and protected-resource metadata. This implementation uses its supported v1 maintenance API (`mcp==1.29.0`), not a handwritten HTTP/JSON-RPC server.

- [Work web and remote MCP](https://learn.chatgpt.com/docs/extend/mcp#use-mcp-backed-tools-in-chatgpt-web)
- [MCP resource-server authentication](https://github.com/modelcontextprotocol/python-sdk/blob/v1.29.0/docs/authorization.md)
- [Streamable HTTP transport](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- [Cloud environment settings and Personal vault](https://learn.chatgpt.com/docs/environments/cloud-environments)

These are supported building blocks, not evidence that this plugin works in an unattended Work run. There is deliberately no placeholder remote URL in plugin.json and no unauthenticated launch command. The next deployment-specific PR must attach the verified HTTPS endpoint to this **same** plugin using the platform's supported packaging, and prove its availability in Work and in its scheduled run.

See [Work packaging review](WORK-PACKAGING.md) for the documented OAuth declaration, a non-installable sketch, and the unresolved discrepancy between OpenAI documentation and the portable MCP schema. No active connection configuration is supplied.

## Ownership and boundaries

Work owns the single schedule, approved Slack report/attachment reads, source deduplication, project disclosure decisions, editing, and publication decisions. Server tools neither read Slack nor invent or schedule content. Work chooses whether to publish every three hours; the engine bridge imposes a minimum six-hour interval between roots per platform/account. A content hash confirms unchanged bytes, **not** permission to disclose a project. Work must apply the user's existing disclosure policy before calling publish.

`Engine` is configured by the trusted host with an existing `state_dir`, its existing `runtime_id`, explicit Threads/Instagram account bindings, registered media assets and `publishing_enabled=False`. Enablement accepts only a boolean; strings such as `"false"` are rejected. None can be supplied or replaced by a tool caller. X is not in the remote schema and cannot be selected. One configured platform does not require credentials for the other.

The host calls `create_server(engine, verifier=..., issuer_url=..., resource_url=..., allowed_clients=..., allowed_subjects=..., allowed_origins=...)` and exposes `create_app(server)` as ASGI behind HTTPS. All URLs must be fixed HTTPS values; the resource path is `/mcp`. The host supplies an **existing approved OAuth TokenVerifier** that validates issuer/signature or securely introspects tokens. No sample verifier is a production verifier. Without a verifier, clients and owner subjects, construction fails. Expiry, audience/resource, client and owner are also checked at the boundary. Read operations need `moru:read`; publication additionally needs `moru:publish`. HTTP Origin and Host checks stay enabled. Hosting must disable access/body/header logs containing credentials and avoid request-body telemetry. Token verifier/provider errors are rejected without details.

MCP authorization tokens and Meta account tokens are separate credentials. Do not pass an MCP bearer token to Meta or a Meta token to MCP. Existing Meta apps and their authorized tokens are retained. The engine reads only the selected platform's injected variables. The deployment's **actually bound** Cloud environment Personal vault/Environment variables settings are where its existing references are requested. Installing the plugin, writing key names, or approving MCP access does not inject these variables. For another host, its approved secret settings must be identified before deployment; this PR neither moves nor copies Vault values. Configure/OAuth/refresh are not remote tools.

## State and media

The service must run on the approved cloud executor that can access the **original durable ledger**, not a user's PC or a throwaway scheduled-task filesystem. It opens the original SQLite database and marker; it does not create, import, replace or repair either. Existing device/inode and account checks remain intact. Moving/copying the database would fail these checks and is not a supported workaround. If the historical ledger cannot be located, deployment and publishing remain blocked.

Only one server writer may own that account/ledger. All remote operations use a nonblocking exclusive lock on the existing marker. This serializes transport calls but does not make legacy CLI writers participate; old writers/schedules must stay disabled. Do not deploy independent replicas with copied SQLite files, or put this SQLite design on an unverified network filesystem. A shared database migration is a separate, explicitly reconciled change, not implemented here.

The bridge keeps existing content/job deduplication and unknown/processing semantics. It blocks new jobs while another job is unresolved and does not let a new ID evade a failed content record. Same-ID resumes continue through the original engine. The six-hour check conservatively uses existing `updated_at` receipts, not guessed publication times. Historical UI posts absent from the ledger still require the existing reconciliation policy.

Minimum media scope is Threads text and Instagram Unified resumable Reels. Work supplies final text and an opaque registered `media_id`, never a path/URL. The trusted host registers an already-reviewed immutable private MP4 with its SHA-256 and real/AI media metadata. A verified ffprobe profile is required even for planning; missing ffprobe, changed hashes, symlinks and non-private file permissions fail closed. Original engine upload checks remain in use. Media staging from Work/Slack to the approved cloud host is **not implemented**: select and verify an approved transfer/storage adapter before enabling Reels. No sample media is posted. Threads video and replies are intentionally not exposed by this first transport.

## Tools

| Tool | What it proves or does |
|---|---|
| moru_status | Key names/format only. Account, ledger and scheduled-run flags remain false. |
| moru_check | Existing ledger and configured account identity through a read-only API call. Not publish permissions or scheduled execution. |
| moru_plan | Existing-state check and final text/media digest, no API calls or ledger writes. `publish_ready` stays false. |
| moru_publish | Disabled until the trusted host explicitly enables it after operational gates; exact reviewed hash, scope, fixed account, ledger lock, duplicate/interval gates, existing engine. |
| moru_metrics | Existing successful job only; official owner metrics through the existing engine. |

## Isolated verification

Install `remote/requirements.txt` in a development virtual environment. From the repository root:

```bash
python3 -m unittest discover -s plugins/moru-sns/remote -p 'test_*.py'
python3 -m unittest discover -s plugins/moru-sns/skills/sns-direct-publisher/scripts -p 'test_*.py'
python3 scripts/check_package.py
```

Tests use synthetic MCP/Meta credentials, in-process ASGI requests, temporary fixture ledgers and fake Meta responses. The initialization helper is called only by test fixtures. The remote suite also launches separate Python processes over one on-disk fixture ledger: an overlapping writer makes zero API calls, and a later process reuses the recorded receipt without publishing again. The official MCP SDK client completes initialization, tool discovery and a tool call through in-process Streamable HTTP; raw protocol tests cover protected-resource discovery, notifications, protocol-version rejection and scopes. These tests do not prove network-filesystem lock semantics or a deployed HTTPS/OAuth flow. Successful tests are not a live account, Work connection, media transfer, persistent cloud state or scheduled-run verification.

## Deployment gates still required

1. Locate the original receipt-bearing ledger and the approved cloud executor that owns it. Keep its binding intact; no new ledger.
2. Identify an existing authorized HTTPS host and OAuth issuer/verifier, allowed Work client and owner. No new paid service or app is provisioned by this proposal.
3. Bind the existing Meta secret references in that actual runtime's official settings; restore approved API-host access and validate identities there.
4. Provide an approved immutable-media staging adapter for Reels. Check hash, actual video and disclosure scope in Work.
5. Add the real endpoint to this repository's existing plugin through a reviewed PR, then install that merged distribution in Work web.
6. Run the one Work-owned schedule with publication disabled and verify plugin version, MCP authorization, both fixed accounts, original ledger, allowed Slack reports and original attachment bytes in that actual unattended run. Only then enable already-authorized publication. A hand-triggered test does not satisfy this gate.
