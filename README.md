# Nanobot Home Assistant Add-on

A Home Assistant add-on repository that runs [nanobot](https://github.com/niradler/nanobot)
as a secured, fully-configurable personal AI agent inside Home Assistant.

## What it is

The **Nanobot** add-on packages nanobot as a long-running personal AI agent that
lives next to Home Assistant:

- **WebUI behind HA Ingress** — the nanobot WebUI is served through Home
  Assistant Ingress, so it reuses your HA login (HA SSO). No extra password, no
  open port. (`ingress: true`, `ingress_port: 8765`.)
- **Home Assistant control via MCP** — read-only by default. nanobot can talk to
  HA through the built-in HA *Model Context Protocol Server* (SSE) and/or a
  bundled stdio `ha-mcp`. Control is **off** until you turn it on.
- **OpenAI-compatible API** (`/v1/chat/completions`) — internal-only by default;
  optionally publishable on a host port, gated by a required bearer token.
- **Chat channels + MQTT** — Telegram/Discord/Slack/… and an MQTT bridge,
  configured natively in nanobot's own `config.json`.

User-facing install and configuration live in
[`nanobot/DOCS.md`](nanobot/DOCS.md).

### Add the repository

Settings → Add-ons → Add-on store → ⋮ → **Repositories**, then add:

```text
https://github.com/niradler/ha-addons
```

Install the **Nanobot** add-on, set at least `llm_api_key`, and start it.

## How we use the fork

The add-on does **not** install nanobot from PyPI. It builds nanobot from our
fork [`niradler/nanobot`](https://github.com/niradler/nanobot), pinned via the
`NANOBOT_REF` build arg in [`nanobot/build.yaml`](nanobot/build.yaml):

```yaml
args:
  NANOBOT_REF: 161ca572   # commit on the fork's `ha` branch
```

**Why fork instead of wrapping?** Some changes nanobot needs to work well inside
HA do not belong in add-on wrapper code — they belong in nanobot itself (for
example, serving the WebUI correctly under an Ingress path prefix). Those land on
the fork's **`ha` branch**, which tracks upstream `main` plus our HA-specific
fixes.

Pinning `NANOBOT_REF` to a **commit** (rather than the moving `ha` branch) makes
rebuilds deterministic and busts Docker's git-install cache layer on each bump.
Track the moving `ha` branch only for quick local dev.

## Fixes / changes we added

### Fork `ha` branch — WebUI under an Ingress path prefix

Upstream nanobot's WebUI assumes it is served from the site root. Under HA
Ingress it is served from `/api/hassio_ingress/<token>/`, which broke asset and
API URLs. The `ha` branch fixes this (commit `c0dee521`):

- relative `vite` base so built assets resolve against the current path;
- a `getBasePath()` helper that derives the `/api/hassio_ingress/<token>/`
  prefix at runtime;
- base-path-aware REST **and** WebSocket URLs;
- relative brand/asset references.

This is the reason we maintain the fork — none of the reference projects (see
[Reference projects](docs/refs.md)) serve nanobot's native WebUI through Ingress;
they sidestep it with a host port + app login.

### Fork `ha` branch — no WebUI password behind a trusted proxy

Upstream nanobot has a catch-22 for proxied WebUI auth: to be reachable through
HA Ingress the WebUI must bind `0.0.0.0`, but a start-guard refuses to boot
unless a `token`/`tokenIssueSecret` is set — and once a secret is set,
`/webui/bootstrap` demands it, so the WebUI prompts for that secret. Behind HA
Ingress (which already authenticated you via HA SSO) that prompt is redundant.

The `ha` branch adds a `trust_proxy_auth` websocket option (commit `a3f19287`):
when enabled, the `0.0.0.0` start-guard passes and `/webui/bootstrap` issues
tokens without the secret/localhost check. The add-on surfaces this as the
**`webui_auth`** option — **off by default**, so HA SSO is the only gate and the
WebUI opens with no password. Set `webui_auth: true` to additionally require
nanobot's own secret prompt as defense-in-depth.

### Add-on layer

- **nanobot owns its config; the add-on only seeds it.** nanobot's config lives at
  `/config/nanobot/settings/config.json` (workspace at `/config/nanobot/workspace`),
  is **managed by the nanobot WebUI**, and is browsable/editable from the Studio
  Code Server add-on. On **first run only**,
  [`generate-config.py`](nanobot/rootfs/usr/bin/generate-config.py) seeds it with
  the Ingress wiring, secure defaults, and `${VAR}` secret references; after that
  the add-on never rewrites it. The HA add-on **Configuration** is purely add-on
  config (secrets + the first-run seed + the `webui_auth` toggle) — not nanobot's
  own config, which you change in the WebUI.
- **Secrets never hit disk.** Secret options (`llm_api_key`, `ha_token`,
  `api_token`, and `secrets[]`) are injected as environment variables and
  referenced from config as `${VAR}` (e.g. `${HA_TOKEN}`). nanobot resolves them
  in memory at startup and fails fast if a referenced var is unset.
- **Read-only HA, enforced by allow-list.** With `ha_read_only: true` (default),
  the built-in HA MCP registers **only** the read tool `GetLiveContext`. Control
  intents (`HassTurnOn`, `HassTurnOff`, `HassLightSet`, …) are never exposed, so
  the agent physically cannot actuate devices. An allow-list can only
  *under*-grant. HA control is also **off** entirely by default
  (`ha_mcp_mode: off`).
- **No ambient HA credentials.** The add-on does **not** request the
  `homeassistant_api` or `hassio_api` grants, so its supervisor token cannot
  touch HA. The only HA credential is the read-only `ha_token` you choose to
  provide.
- **No privileged / no SYS_ADMIN.** `exec` is off by default and
  `restrictToWorkspace` is on; the overlay never sets `sandbox: bwrap` (which
  would need SYS_ADMIN). If you opt `exec` on, it runs unsandboxed inside the
  workspace.
- **Fail-safe API publish.** Publishing the OpenAI API on a host port
  **requires** `api_token`; the init service refuses to start otherwise.
- **`curl` stays in the final image.** HA's `base-debian:bookworm` does not ship
  `curl`, and `bashio` uses it to reach the Supervisor API. An early build purged
  it, which made every `bashio::config` call fail and crash-looped the gateway
  ("No API key configured for provider 'None'"). `curl` (+ `ca-certificates`) is
  now kept in the runtime layer.

### Security at a glance (config.yaml)

| Concern | Setting |
| --- | --- |
| WebUI access | HA Ingress only (`ingress: true`, port `8765`); no host port; HA SSO is the gate (no nanobot password unless `webui_auth: true`) |
| HA credentials | none ambient — no `homeassistant_api` / `hassio_api` grant |
| HA control | off by default; read-only allow-list (`GetLiveContext`) |
| API | internal by default; host port `8900/tcp` only if published + `api_token` |
| Container | no `privileged`, no `SYS_ADMIN`; `exec` off; `restrictToWorkspace` on |
| Persistence | `addon_config:rw` → `/config` (config + workspace, in HA backups) |

## How to build (current: on-device local add-on)

Today the add-on builds **on the Pi** as a local add-on. Flow:

```bash
# 1. Copy the add-on dir to the Pi at /addons/nanobot
tar -C nanobot -cf - . | ssh -i ~/.ssh/ha_key -p 2222 root@homeassistant.local \
  'mkdir -p /addons/nanobot && tar -C /addons/nanobot -xf -'

# 2. On the Pi: re-scan the local add-on store, then (re)build
ha store reload
ha apps rebuild local_nanobot
```

The first build compiles nanobot + the WebUI on-device and takes several
minutes. Architectures: **`aarch64`** (the Pi) and **`amd64`**.

### Dockerfile stages

See [`nanobot/Dockerfile`](nanobot/Dockerfile):

1. **Base** — `ghcr.io/home-assistant/{arch}-base-debian:bookworm` (glibc, so
   nanobot's wheels — `lxml`, `pydantic-core`, … — install from prebuilt
   manylinux wheels instead of compiling).
2. **Runtime deps (own cached layer)** — `ca-certificates curl git bubblewrap
   python3 python3-venv`. `curl` is required by `bashio`.
3. **Build + purge (single layer)** — add Node 20 + `uv` + a compiler toolchain,
   create `/opt/venv`, then:

   ```bash
   uv pip install "nanobot-ai[api] @ git+https://github.com/niradler/nanobot@${NANOBOT_REF}"
   ```

   `NANOBOT_FORCE_WEBUI_BUILD=1` makes nanobot's hatch build hook bundle the
   WebUI (`nanobot/web/dist`). The build-only toolchain is purged in the same
   layer so it never ships.
4. **rootfs** — copy s6-overlay services (`init-nanobot` oneshot →
   `nanobot-gateway` + `nanobot-api` longruns) and the init scripts. s6-overlay
   from the base image is PID 1 (`init: false`).

## Development flow

### Add-on / Python (`generate-config.py`)

`generate-config.py` is pure stdlib Python and fully unit-tested without HA:

```bash
python -m pytest tests/ -q     # runs on any machine
```

Then sync to the Pi and rebuild (see *How to build* above).

### Fork / WebUI changes

```bash
# Work on the fork's `ha` branch (a git worktree is handy)
cd webui && npm install && npm run build   # or: bun install && bun run build

# Commit + push the `ha` branch, then pin build.yaml to the new commit
#   NANOBOT_REF: <new-commit>
# (bumping the ref also busts the Docker git-install cache)

# Rebuild on the Pi
ha apps rebuild local_nanobot
```

Pinning `NANOBOT_REF` to a commit keeps rebuilds reproducible. The Dockerfile's
default (`ARG NANOBOT_REF=ha`) tracks the moving `ha` branch for quick local dev
only; `build.yaml` always pins an explicit commit for real builds.

## Formalizing: publish a prebuilt image

This is the "we're done with dev" step — **not yet active**. It stops on-device
compilation so fresh instances install in seconds.

1. **Add an `image:` key to `config.yaml`**, e.g.:

   ```yaml
   image: ghcr.io/niradler/ha-addons-nanobot-{arch}
   ```

   When `image:` is present, the Supervisor **pulls** the image tagged with the
   add-on `version` instead of building locally.
2. **CI builds + pushes to GHCR** on a version tag using the official
   `home-assistant/builder` action (matrix over `aarch64`/`amd64`). Make the
   GHCR package **public** so Pis pull without auth.
3. **Pin `NANOBOT_REF`** to a release commit/tag per image build (reproducible).

Fresh-instance install then becomes: add the repo URL → install (fast pull, no
compile) → set `llm_api_key` → start.

### Sketch: `.github/workflows/build.yml`

```yaml
name: Build add-on images
on:
  push:
    tags: ["*"]

jobs:
  build:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        arch: [aarch64, amd64]
    permissions:
      contents: read
      packages: write
    steps:
      - uses: actions/checkout@v4

      - name: Log in to GHCR
        uses: docker/login-action@v3
        with:
          registry: ghcr.io
          username: ${{ github.actor }}
          password: ${{ secrets.GITHUB_TOKEN }}

      - name: Build & push (${{ matrix.arch }})
        uses: home-assistant/builder@master
        with:
          args: >-
            --${{ matrix.arch }}
            --target nanobot
            --image "ha-addons-nanobot-{arch}"
            --docker-hub ghcr.io/niradler
            --addon
```

## Repository layout

```text
repository.yaml          Repo metadata (name, url, maintainer)
README.md                This file
nanobot/                 The add-on
  config.yaml            Manifest: arch, ingress, ports, options, schema, map, grants
  build.yaml             Per-arch base image + NANOBOT_REF
  Dockerfile             Build stages (base -> deps -> uv install fork -> rootfs)
  DOCS.md                User-facing config + security
  CHANGELOG.md
  translations/          Option labels + help text
  icon.png / logo.png
  rootfs/                s6 services + nanobot-init + generate-config.py
tests/                   pytest for the runtime-config overlay generator
docs/                    Design spec + reference-project comparison
```

## Testing

- **Add-on overlay generator** — `python -m pytest tests/ -q` exercises
  [`generate-config.py`](nanobot/rootfs/usr/bin/generate-config.py) (27 tests:
  overlay merge precedence, secret-ref injection with no plaintext in output,
  SSRF-whitelist gating, fail-safe API-token rule, read-only allow-list,
  WebUI auth toggle, idempotency, token-secret persistence). Pure Python — no
  HA required.
- **WebUI** — vitest on the fork's `ha` branch.

## See also

- [Reference projects](docs/refs.md) — prior art reviewed and how our design
  compares.
- [Design spec](docs/2026-06-13-nanobot-ha-addon-design.md) — decisions,
  process model, security posture, risks.
- [User docs](nanobot/DOCS.md) — install + configuration.
