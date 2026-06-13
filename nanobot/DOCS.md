# Nanobot add-on

Runs [nanobot](https://github.com/niradler/nanobot) (the `niradler` fork, built
from the `ha` branch) as a long-running, secured, fully-configurable personal AI
agent inside Home Assistant.

- **WebUI** behind HA Ingress (uses your HA login — no extra auth).
- **Home Assistant control** via MCP (built-in HA MCP Server over SSE, and/or a
  bundled stdio `ha-mcp`).
- **OpenAI-compatible API** (`/v1/chat/completions`), internal by default.
- **Chat channels** (Telegram/Discord/Slack/…) + MQTT, configured natively.

## Install

1. Add this repository to Home Assistant
   (Settings → Add-ons → Add-on store → ⋮ → Repositories):
   `https://github.com/niradler/nanobot-addon`
2. Install the **Nanobot** add-on. The first build compiles nanobot + the WebUI
   on the device and can take several minutes.
3. In **Configuration**, set at least `llm_api_key` (and `llm_provider` /
   `llm_model`) so the agent has a model to talk to.
4. Start the add-on and open the WebUI from the sidebar.

## Configuration model

The source of truth is your native nanobot **`config.json`**, which lives in the
add-on's config directory (`/addon_configs/<slug>/config.json`, editable from the
Studio Code Server / Samba add-ons). A starter `config.json` is seeded on first
run and **never overwritten**.

At each start the add-on writes a disposable `config.runtime.json` (under `/data`)
by layering a thin overlay on top of your `config.json`:

- WebUI (`channels.websocket`) wired for Ingress (bind `0.0.0.0:8765`, a
  generated `tokenIssueSecret`, `websocketRequiresToken: false`).
- HA MCP servers (`tools.mcpServers.ha_builtin` / `ha_mcp`) per `ha_mcp_mode`.
- `tools.ssrfWhitelist` for HA-internal ranges — **only** when the SSE HA MCP
  endpoint is enabled (stdio MCP is SSRF-exempt).
- `tools.restrictToWorkspace`, `tools.exec.enable`.
- `api` host/port when the API is enabled.

**The overlay only fills gaps — anything you set in `config.json` wins.** Put
providers, model presets, agents, channels, web tools, etc. directly in
`config.json`.

## Secrets

Secret options (`llm_api_key`, `ha_token`, `api_token`, and `secrets[]`) are
exported as environment variables and referenced from `config.json` as `${VAR}`
(e.g. `${LLM_API_KEY}`, `${HA_TOKEN}`). They are never written to disk in
plaintext. nanobot fails fast if a referenced variable is unset.

## Home Assistant control

HA control is **off by default** (`ha_mcp_mode: off`) — on first boot nanobot has
no access to Home Assistant at all.

- **builtin** — enable HA's *Model Context Protocol Server* integration, create a
  long-lived token and put it in `ha_token`, set `ha_mcp_mode: builtin`. nanobot
  connects directly to HA core (`http://homeassistant:8123/mcp_server/sse` by
  default; override with `ha_url`) using that token. HA-internal CIDRs are
  SSRF-whitelisted only while this SSE endpoint is enabled.
- **ha-mcp** — stdio MCP server (SSRF-exempt). The concrete implementation /
  version is pinned in a later release; `command`/`args`/`enabledTools` are
  configurable.

### Read-only (default: nanobot cannot change anything)

With `ha_read_only: true` (default), the built-in HA MCP registers **only the
read tool `GetLiveContext`**. Control intents (`HassTurnOn`, `HassTurnOff`,
`HassLightSet`, …) are never exposed to the agent, so it physically cannot
actuate devices or change HA state. It is an allow-list, so it can only
under-grant — it never accidentally permits control.

Set `ha_read_only: false` only when you explicitly want write access ("the cat
gets claws").

## Security

- **No ambient HA credentials.** The add-on does **not** request `homeassistant_api`
  or `hassio_api`, so its supervisor token cannot touch Home Assistant. The only
  HA credential is the read-only `ha_token` you choose to provide.
- WebUI is reachable only through HA Ingress (behind your HA login) — no extra
  WebUI password, no exposed port. nanobot's own WebUI auth is disabled by
  default since HA SSO is the gate; set `webui_auth: true` to also require
  nanobot's secret prompt.
- The API and channels are internal-only unless you explicitly publish them.
- Publishing the API on a host port **requires** `api_token` (fail-safe: the
  add-on refuses to start otherwise).
- `exec` is disabled by default. If enabled it runs **unsandboxed** — the add-on
  is not granted `SYS_ADMIN`, so bubblewrap isn't used. `restrictToWorkspace`
  keeps file/shell tools inside the workspace.
- No `privileged`. Secrets are env-injected and referenced as `${VAR}`; never
  written to disk in plaintext.
- Persistent data (`config.json`, `workspace/`) is included in HA backups.
- Planned hardening: shipped AppArmor profile (tracked for a later release).

## Notes / current limitations

- `nanobot serve` does not yet enforce `api_token` itself — token enforcement is
  a planned `ha`-branch change. Keep the API unpublished until then.
- WebUI under the Ingress path prefix depends on the fork's `ha`-branch base-path
  handling.
