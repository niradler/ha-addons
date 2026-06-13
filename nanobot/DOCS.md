# Nanobot add-on

Runs [nanobot](https://github.com/niradler/nanobot) (built from the `niradler`
fork) as a long-running, secured, personal AI agent inside Home Assistant.

- **WebUI** behind HA Ingress (uses your HA login — no extra password).
- **Home Assistant control** via MCP — read-only by default (see below).
- **OpenAI-compatible API** (`/v1/chat/completions`), internal by default.
- **Chat channels** (Telegram/Discord/Slack/…) + MQTT, configured in nanobot.

## Install

1. Add the repository `https://github.com/niradler/ha-addons`
   (Settings → Add-ons → Add-on store → ⋮ → Repositories).
2. Install **Nanobot** (pulls a prebuilt image — no on-device build).
3. In **Configuration**, set `llm_api_key` (and `llm_provider` / `llm_model` if
   you don't want the defaults), then **Start**.
4. Open the WebUI from the sidebar and configure the rest in **Settings**.

## What the add-on configures vs. what you configure

The add-on **Configuration** tab is intentionally small — it holds only
add-on-level concerns:

| Option | Purpose |
|--------|---------|
| `llm_provider` / `llm_model` | Provider + model to **seed** on first run so the gateway can boot. Change providers later in the WebUI. |
| `llm_api_key` | Secret → `${LLM_API_KEY}` env. |
| `ha_token` | HA long-lived token → `${HA_TOKEN}` env (for HA MCP). |
| `api_token` | Bearer token required if the API is published. |
| `secrets[]` | Extra `NAME=value` secrets → env, for `${NAME}` refs. |
| `webui_auth` | `false` (default) = HA SSO only; `true` = also require nanobot's WebUI secret. |
| `api_enabled` / `api_publish_port` / `api_port` | Whether the add-on runs / publishes the OpenAI API. |
| `log_level` | Gateway log verbosity. |

**Everything else is nanobot's own config** — providers, models, **MCP servers
(including HA control)**, tools, channels, web search, etc. Manage it in the
nanobot **WebUI → Settings**, or edit the config file directly (see below). The
add-on seeds it once on first run and never overwrites it after.

## Editing nanobot's config / skills

nanobot's config and workspace live under the add-on's config directory, which
is editable from the **Studio Code Server** (or Samba) add-on:

```text
/addon_configs/<slug>/nanobot/settings/config.json   # nanobot config
/addon_configs/<slug>/nanobot/workspace/skills/       # your skills
/addon_configs/<slug>/nanobot/workspace/{memory,sessions,cron}/
```

`<slug>` is the installed add-on slug (e.g. `local_nanobot` for a local build, or
a repo-hash like `bf7e1151_nanobot` for a store install). Open the
`/addon_configs` root in Studio Code Server to find it.

## Home Assistant control (HA MCP) — read-only

HA control is **not** wired by the add-on; you add it to nanobot's config. It's
**off until you do.** Setup:

1. **Enable HA's MCP Server integration** — Settings → Devices & Services → Add
   Integration → **Model Context Protocol Server**. Scope what it exposes via
   what you expose to **Assist**.
2. **Provide a token** — create a long-lived token (ideally for a dedicated
   read-only HA user) and put it in the add-on option **`ha_token`** (injected as
   `${HA_TOKEN}`, never written to disk).
3. **Add the MCP server to nanobot** — in the WebUI **Settings → MCP servers**,
   or by editing `config.json`:

   ```json
   {
     "tools": {
       "mcpServers": {
         "ha_builtin": {
           "type": "sse",
           "url": "http://homeassistant:8123/mcp_server/sse",
           "headers": { "Authorization": "Bearer ${HA_TOKEN}" },
           "enabledTools": ["GetLiveContext"]
         }
       },
       "ssrfWhitelist": ["172.30.32.0/23", "127.0.0.1/32"]
     }
   }
   ```
4. **Restart** the add-on (MCP connections are made at startup). The log shows
   `MCP server 'ha_builtin': connected`.

### Read-only enforcement

`"enabledTools": ["GetLiveContext"]` registers **only** the read tool. Control
intents (`HassTurnOn`, `HassTurnOff`, `HassLightSet`, …) are never exposed to the
agent, so it physically cannot actuate devices — an allow-list can only
*under*-grant. For write access, add the control tools you want (and ideally use
a token whose HA user is allowed to perform them). `ssrfWhitelist` is required
because the SSE endpoint is on an HA-internal address.

## Secrets

`llm_api_key`, `ha_token`, `api_token`, and `secrets[]` are injected as
environment variables and referenced from `config.json` as `${VAR}` (e.g.
`${LLM_API_KEY}`, `${HA_TOKEN}`). They are never written to config in plaintext.
nanobot fails fast at startup if a referenced variable is unset.

## Security

- **No ambient HA credentials.** The add-on does not request `homeassistant_api`
  or `hassio_api`; its supervisor token cannot touch HA. HA access only via the
  `ha_token` you provide.
- WebUI is reachable only through HA Ingress (behind your HA login); no exposed
  port, and (default) no extra nanobot password.
- API/channels are internal-only unless you publish them. Publishing the API on
  a host port **requires** `api_token` (fail-safe: the add-on refuses to start
  otherwise).
- Secure defaults seeded: `restrictToWorkspace: true`, `exec` off. Change them in
  the WebUI if you need to; `exec` would run unsandboxed (no `SYS_ADMIN`).
- No `privileged`. Persistent data (`config.json`, `workspace/`) is in HA backups.

## Backups

The add-on's config + workspace (skills, memory, sessions) are included in HA
backups (full backups always; partial when the add-on is selected). On restore,
HA re-pulls the image and restores your data.
