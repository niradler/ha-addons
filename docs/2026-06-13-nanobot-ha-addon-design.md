# Nanobot Home Assistant Add-on — Design Spec

**Date:** 2026-06-13
**Status:** Approved shape, pending written-spec review
**Repo target:** `c:\Projects\ha\nanobot-addon`

## 1. Goal

Build our own Home Assistant add-on that runs [nanobot](https://github.com/niradler/nanobot)
(our fork) as a long-running, secured, fully-configurable personal AI agent inside HassOS on the
Raspberry Pi. The add-on must support:

- WebUI via HA Ingress (behind HA auth).
- Home Assistant control via MCP — both the built-in HA MCP Server and a bundled `ha-mcp`,
  configurable.
- OpenAI-compatible API (`/v1/chat/completions`).
- Chat channels (Telegram/Discord/Slack/etc.) + MQTT bridge.
- Full nanobot configurability with secure secret handling.

"Our own version so we can customize" is the driving principle: the add-on builds nanobot **from
our fork**, so anything nanobot cannot yet do for HA (e.g. serving the WebUI under an Ingress path
prefix) is fixed on a dedicated **`ha` branch** of the fork rather than worked around in wrapper code.

## 2. Key decisions (locked)

| Decision | Choice |
|---|---|
| nanobot source | Build from `niradler/nanobot` git, pinned to ref `NANOBOT_REF` (default branch `ha`) |
| Capabilities (v1) | WebUI (Ingress) + HA-control-via-MCP + OpenAI API + chat channels + MQTT |
| HA control mechanism | Both, configurable: built-in HA MCP Server **and** bundled `ha-mcp` |
| Config surface | Native nanobot `config.json` is the source of truth; add-on layers thin add-on-specific files/overlays on top |
| API/channel exposure | Internal + Ingress only by default; optional published host port for the API, gated by a **required** bearer token (fail-safe) |
| Secrets | Native HA add-on options (admin-only `options.json`), injected as `${VAR}` env into nanobot config — never written to disk |
| Architectures | `aarch64` (the Pi) + `amd64` |
| Process model | s6-overlay services: `init` (oneshot) → `gateway` (longrun) + `api` (longrun) |

## 3. Background facts about nanobot (from docs/quick-start.md, docs/configuration.md)

- Python 3.11+. Installed via `pip`/`uv` (`nanobot-ai`) or from source. Our add-on installs from
  source (our fork) with `uv`.
- Config file: `<NANOBOT_HOME>/config.json` (camelCase). Workspace: `<NANOBOT_HOME>/workspace/`
  (memory, sessions, cron, **skills/**, artifacts).
- Commands: `nanobot onboard`, `nanobot status`, `nanobot agent`, `nanobot gateway`, `nanobot serve`.
  `-c/--config` and `-w/--workspace` override paths.
- **WebUI** is the `websocket` channel: `channels.websocket.enabled`, `channels.websocket.port`
  (default 8765), `channels.websocket.tokenIssueSecret`. Health endpoint is **18790** (distinct from
  the WebUI port 8765).
- **Secrets**: any string value supports `${VAR}`, resolved once at startup, in memory only. Unset
  referenced var → fail-fast at startup.
- **MCP** (`tools.mcpServers.<name>`): two transports — **stdio** (`command`+`args`, SSRF-exempt) and
  **HTTP/SSE** (`url`+`headers`, SSRF-guarded). Supports `enabledTools`, `toolTimeout`.
- **SSRF guard** blocks loopback, RFC1918/private, CGNAT/Tailscale, link-local, and cloud-metadata
  for built-in web fetch **and HTTP/SSE MCP**. Exempt with `tools.ssrfWhitelist` (prefer single-host
  CIDRs). **Stdio MCP is not affected.**
- **Security knobs**: `tools.restrictToWorkspace` (default false; set true), `tools.exec.sandbox`
  (`"bwrap"` requires `SYS_ADMIN` cap + Linux namespaces), `tools.exec.enable`, `channels.*.allowFrom`,
  pairing (DM code-exchange access control).
- **Heartbeat** cron runs by default under `gateway` (reads `HEARTBEAT.md` in the workspace).

## 4. Repository layout

Standard HA add-on **repository** layout so it can be added by git URL as a custom repo:

```text
nanobot-addon/
  repository.yaml                      # repo metadata (name, url, maintainer)
  README.md                            # repo-level: how to add + install
  docs/2026-06-13-nanobot-ha-addon-design.md   # this spec
  nanobot/                             # the add-on
    config.yaml                        # manifest: arch, ingress, ports, options, schema, maps, grants
    build.yaml                         # per-arch base image + build args (NANOBOT_REF, etc.)
    Dockerfile                         # FROM HA base-python; uv pip install git+fork@${NANOBOT_REF}
    rootfs/
      etc/s6-overlay/s6-rc.d/
        init-nanobot/                  # oneshot: workspace, config merge, secrets, MCP/SSRF wiring
        nanobot-gateway/               # longrun: nanobot gateway (WebUI + channels + heartbeat)
        nanobot-api/                   # longrun: nanobot serve (OpenAI API), conditional on toggle
        user/contents.d/               # service bundle wiring
      usr/bin/
        nanobot-init                   # bashio entrypoint for init-nanobot
        generate-config.py             # build the add-on overlay + write final config
      etc/cont-init.d/ (if needed)
    translations/en.yaml               # option labels + help text
    icon.png  logo.png  DOCS.md  CHANGELOG.md
```

## 5. Configuration model (native config + thin overlay)

**Source of truth:** the user's native nanobot `config.json`, stored in the add-on's persistent,
user-editable config directory (editable via the VSCode/Samba add-ons). The add-on never rewrites the
user's structure or maps a lossy UI schema over it.

**Merge precedence at startup** (`generate-config.py`):

1. **User native `config.json`** (full nanobot fidelity) — base.
2. **Add-on overlay** — only add-on-managed keys, shallow-merged on top, and only when the add-on
   "owns" them (each is individually skippable if the user set it themselves):
   - `channels.websocket.enabled=true`, `port`, `tokenIssueSecret` (generated/persisted) — for Ingress WebUI.
   - `tools.mcpServers.<ha…>` — HA built-in MCP (SSE) and/or `ha-mcp` (stdio), per options.
   - `tools.ssrfWhitelist` — auto-append HA-internal CIDRs (e.g. `172.30.32.0/23`, `127.0.0.1/32`)
     **only** when an HTTP/SSE HA MCP endpoint is enabled.
   - `tools.restrictToWorkspace=true`, `tools.exec.sandbox` policy (see §7).
   - serve/API host+port binding for `nanobot serve`.
3. **Secrets** — never merged into JSON on disk. Add-on options that are secrets are exported as env
   vars; the overlay references them as `${VAR}` so resolution happens in nanobot at startup.

The merged result is written to a runtime config path (e.g. `<NANOBOT_HOME>/config.runtime.json`) and
passed via `nanobot --config`, leaving the user's authored `config.json` pristine. (Alternative:
write back into `config.json` — rejected, because it would clobber user formatting/comments and fight
nanobot's own `onboard`/WebUI writes.)

**HA add-on options (UI) — intentionally small**, covering only add-on concerns:

- Secrets: provider API keys, channel tokens, HA long-lived token (for built-in MCP).
- HA control: `ha_mcp_mode` (`off` | `builtin` | `ha-mcp` | `both`), `ha_read_only` (default `true`).
- API: `api_enabled`, `api_publish_port` (default false), `api_port`, `api_token` (required iff publish).
- Safety: `exec_enabled` (default false), `restrict_to_workspace` (default true).
- Misc: `log_level`, `nanobot_ref` (advanced override of the pinned ref).

Everything else (providers, model presets, agents, channels detail, web tools, gateway, transcription)
lives in the native `config.json`.

## 6. Process model & data flow

s6-overlay (PID1 via HA base image; `init: false` in manifest):

1. **init-nanobot** (oneshot):
   - Ensure persistent workspace + `config.json` exist (seed a minimal template on first run; never
     overwrite an existing one).
   - Read add-on options via `bashio::config`; export secrets as env.
   - Run `generate-config.py` → write `config.runtime.json` (overlay + secret refs).
   - Generate/persist `tokenIssueSecret` if absent.
   - Validate fail-safe rules (e.g. API publish without token → log error + exit non-zero).
2. **nanobot-gateway** (longrun): `nanobot gateway -c <runtime> -w <workspace>` → WebUI (8765) + chat
   channels + MQTT + heartbeat. Ingress proxies 8765. Health on 18790.
3. **nanobot-api** (longrun, conditional): `nanobot serve -c <runtime> -w <workspace> --host …` on the
   API port. Bound to internal interface unless `api_publish_port` + `api_token`.

## 7. Security posture

- **Ingress-first**: WebUI only reachable through HA Ingress (HA login). API + channels reachable only
  on the internal Docker network unless explicitly published.
- **API publish is fail-safe**: if `api_publish_port=true` and `api_token` is empty, the init service
  refuses to start (clear error). Published API requires the bearer token.
- **HA control read-only by default** (`ha_read_only=true`): built-in HA MCP scoped to Assist-exposed
  entities; `ha-mcp` started in its read-only mode / with a read-only tool subset via `enabledTools`.
- **Secrets** in `options.json` (admin-only, never committed), env-injected, `${VAR}` in config,
  never written to disk.
- **Tool safety**: `restrictToWorkspace=true` by default. `exec` disabled by default; if enabled and
  `SYS_ADMIN` is unavailable, do **not** silently weaken the container — run exec unsandboxed only
  when the user explicitly opts in, documented as such. (Decision point at impl time: whether to grant
  `SYS_ADMIN` to support `bwrap`; default = no.)
- **SSRF**: only the specific HA-internal CIDRs are whitelisted, and only when HTTP/SSE HA MCP is on.
  Prefer `ha-mcp` over stdio to avoid widening SSRF at all.
- No `privileged`; minimal `hassio_api`/`homeassistant_api` grants (only what built-in MCP / token
  retrieval needs); AppArmor profile shipped.

## 8. HA control wiring

- **Built-in HA MCP Server** (default `builtin`): requires the user to enable HA's "Model Context
  Protocol Server" integration. nanobot connects as an SSE client to the HA core MCP endpoint using a
  long-lived token (add-on option) — added to `tools.mcpServers.ha_builtin` with the matching
  `ssrfWhitelist` entry. Scope is whatever is exposed to Assist (read-only-friendly).
- **ha-mcp** (`ha-mcp` or `both`): bundled/launched as a **stdio** MCP server (SSRF-exempt), given the
  HA token/URL via env. Read-only default via its own flag or `enabledTools` subset. Concrete `ha-mcp`
  implementation + version to pin is an **impl-time decision** (candidates evaluated then; pinned in
  `build.yaml`/overlay).

## 9. Targets, persistence, packaging

- **arch:** `aarch64`, `amd64`. Base: HA `ghcr.io/home-assistant/{arch}-base-python` (Debian-based,
  to match nanobot's apt deps: `bubblewrap`, `git`, `nodejs` for the optional WhatsApp bridge).
- **Persistence (verified against the live instance):** `NANOBOT_HOME` = native `config.json` +
  `workspace/` (memory, sessions, cron, **skills/**, artifacts) lives under **`addon_config:rw`**,
  which the supervisor exposes on the host at `/addon_configs/<slug>/` and mounts **into the container
  as `/config`**. This single location is:
  - **editable from the user's Studio Code Server add-on** (`a0d7b954_vscode`) — it appears in the
    VSCode file tree under `/addon_configs/<slug>/`, alongside the other shared mounts;
  - **included in HA backups** — `addon_config` is backed up with the add-on (full backups always;
    partial backups when the add-on is selected). The user's Google Drive Backup add-on runs full
    backups, so it is captured automatically.
  - Confirmed live: `/addon_configs/` already contains per-add-on folders (zigbee2mqtt, cloudflared,
    appdaemon, matter_server); `/share` exists.
  - Regenerated `config.runtime.json` + transient caches live under private **`/data`** (also backed
    up with the add-on, but disposable/regenerated each boot — kept out of the browsable dir).
  - Optional large media → `/share` (only in backups if the share folder is selected, so not used for
    anything that must be backed up).
  - Historical note: a resolved supervisor partial-backup quirk with `addon_configs`
    (home-assistant/supervisor#4915) never affected full backups.
- **Install:** add the repo by git URL → install the `nanobot` add-on.

## 10. Testing strategy

- **Unit (runs on Windows, no HA):** `generate-config.py` — overlay merge precedence, secret-ref
  injection (no plaintext in output), SSRF-whitelist gating, fail-safe API-token rule, idempotent
  re-runs. Pure Python + pytest.
- **Static:** `hadolint` (Dockerfile), `shellcheck` (s6 scripts), `yamllint` (config.yaml/build.yaml),
  add-on schema sanity.
- **Build:** `docker build` per arch with `--build-arg NANOBOT_REF`.
- **Smoke (on the Pi or a HassOS test):** add-on starts → `nanobot status` OK → health 18790 responds
  → WebUI loads through Ingress → one test agent message round-trips → HA MCP tool list visible.
- **Security checks before "done":** no secrets in `config.runtime.json`; API not on LAN unless
  published+token; read-only HA control verified by attempting a control action and confirming refusal.

## 11. Risks & mitigations

| Risk | Mitigation |
|---|---|
| WebUI breaks under Ingress path prefix / WS proxy | Verify early; if broken, patch the WebUI base-path handling on the fork's `ha` branch (the reason we fork) |
| `bwrap` needs `SYS_ADMIN` not granted to add-ons | Default exec off + `restrictToWorkspace`; opt-in unsandboxed exec, documented; revisit granting `SYS_ADMIN` only if needed |
| SSRF guard blocks HA MCP over SSE | Auto-whitelist HA-internal CIDRs only when SSE HA MCP enabled; prefer stdio `ha-mcp` |
| `addon_config` mount path differs across HA base versions | Confirm against current base image during impl; keep path in one place |
| nanobot `gateway` writes back to config / fights overlay | Run from `config.runtime.json`, keep user `config.json` as untouched source of truth |
| Build pulls from a moving `ha` branch | Pin `NANOBOT_REF` to a tag/commit for releases; `ha` branch only as the default dev ref |

## 12. Out of scope (v1)

- HA Conversation-agent / Assist pipeline registration (can layer on the OpenAI API later).
- Multi-instance / multiple bots in one add-on.
- Voice/transcription tuning beyond passing through native config.
- Auto-publishing the repo / add-on store listing.
