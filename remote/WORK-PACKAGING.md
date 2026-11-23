# Work web packaging review

Status: documentation only, checked 2026-11-23. No endpoint, OAuth connection, registration ID or original ledger host has been verified. This file is not loaded as configuration.

## What the sources say

[OpenAI packaging documentation](https://developers.openai.com/plugins/build/plugins#configure-mcp-authentication) places remote transport in root `mcp.json`, with `type: "streamable-http"` and a URL. Server-specific `extensions.com.openai.auth` declares OAuth. This is distinct from root `plugin.json`'s `extensions.com.openai.apps`, which references registered server mappings. Secrets do not belong in either file.

[Work documentation](https://learn.chatgpt.com/docs/extend/mcp#use-mcp-backed-tools-in-chatgpt-web) requires installed hosted plugin tools; local Codex configuration is not consumed by Work web.

[Connection documentation](https://developers.openai.com/plugins/deploy/connect-chatgpt) separates endpoint testing, registration and installed-plugin evaluation. [OAuth documentation](https://developers.openai.com/plugins/build/auth) requires discovery, authorization-code/PKCE and token validation. Exact client and callback identifiers come from the connection management page.

## Schema discrepancy: unresolved

The current [portable MCP schema](https://agent-plugins.org/schemas/1.0.0/mcp.schema.json), `$defs.streamableHttpServer`, lists only `type`, `url` and `headers`, and sets `additionalProperties: false`. It therefore rejects the server-level `extensions` object documented by OpenAI. The root [plugin schema](https://agent-plugins.org/schemas/1.0.0/plugin.schema.json) allows extension namespaces, but that does not authorize moving server authentication into the root manifest.

This is a documentation/schema mismatch, not a tested registration failure. Do not silently remove authentication, claim schema validation passed, change schema versions speculatively, or treat an `ON_INSTALL` marketplace policy as an OAuth integration. Resolve the supported importer/schema combination before a release adds active MCP configuration.

## Non-installable configuration sketch

The following demonstrates the documented nesting only. Angle-bracket values are deliberately unresolved and are **not URLs or registered identifiers**. Do not copy this block into the installed plugin. No `mcp.json`, `.app.json`, compatibility manifest or endpoint is added by this PR.

```json
{
  "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
  "mcpServers": {
    "moru": {
      "type": "streamable-http",
      "url": "<VERIFIED_EXISTING_HTTPS_MCP_ENDPOINT>",
      "extensions": {
        "com.openai": {
          "auth": {
            "type": "oauth",
            "baseScopes": ["moru:read"]
          }
        }
      }
    }
  }
}
```

The sketch intentionally does not select DCR, CIMD or a provided client: no approved issuer/client binding exists in the verified configuration. Discovery must be checked against that existing binding. It requests read scope only; publication additionally requires `moru:publish` and trusted server enablement. OAuth discovery advertising publish scope does not itself grant it. Keep `publishing_enabled=False` throughout connection verification.

## Repository-to-runtime mapping

| Boundary | Current evidence | Required before operational acceptance |
|---|---|---|
| Public package | `plugin.json` 0.1.1 and repo marketplace; no remote registration | Review versioned changes in this same repository; verify the actual Work installation |
| Remote transport | Optional `server.create_server` / `create_app`; no listener | Verified HTTPS endpoint and compatible importer; preserve OAuth and owner/client checks |
| Registered mapping | No `.app.json` or `plugin_asdk_app` mapping | Use only an observed, approved connection ID; never invent one |
| Publisher | `engine.Engine` invokes the existing publisher | Fixed Threads/Instagram accounts and original durable state; no migration or replacement |
| Credentials | Host reads selected platform's injected references | Actual host settings and OAuth verifier; no tokens in manifests, headers or examples |
| Work workflow | Work owns Slack attachments, editorial decisions and scheduling | Installed tools available in the real unattended run; no local-process fallback |

No engine changes are required by this document. Existing server tools cover Threads text and Instagram registered Reels only. Media staging and the original ledger's reachable owner remain unresolved. A later integration change must also route the existing skills to those registered tools in Work; local CLI instructions alone cannot establish web operation.

## Review evidence and acceptance limits

Compared both published schemas with the documented field placement and current source. Confirmed that neither active manifest nor marketplace gains an endpoint, app mapping, token or startup command. The JSON sketch parses, but is intentionally not deployable; portable-schema compliance and Work importer acceptance are **not claimed**. Prior engine/transport tests remain applicable because this change only adds documentation. No accounts, hosts, registrations, schedules or ledgers were created, and no API publication was attempted.
