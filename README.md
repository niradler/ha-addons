# Nir's Home Assistant Add-ons

A Home Assistant **add-on repository** (monorepo). Add it once, then install any
add-on inside it from the HA Add-on store.

## Install

1. In Home Assistant: **Settings → Add-ons → Add-on store → ⋮ (top right) →
   Repositories**, and add:

   ```text
   https://github.com/niradler/ha-addons
   ```

2. The store now lists the add-ons below. Open **Nanobot** → **Install**. It
   **pulls** a prebuilt image (no on-device build, ~1–2 min).
3. Open the add-on's **Configuration** tab, set `llm_api_key` (your
   OpenAI/OpenRouter/etc. key), then **Start**.
4. Open the WebUI from the sidebar (it's behind your HA login — no extra
   password). See [`nanobot/DOCS.md`](nanobot/DOCS.md) for full configuration.

## Add-ons in this repository

| Add-on | Description | Docs |
|--------|-------------|------|
| **[Nanobot](nanobot/)** | Secured, fully-configurable personal AI agent — WebUI behind HA Ingress, read-only HA control via MCP, OpenAI-compatible API, chat channels + MQTT. Built from the [`niradler/nanobot`](https://github.com/niradler/nanobot) fork. | [README](nanobot/README.md) · [DOCS](nanobot/DOCS.md) |

More add-ons can be added as sibling folders (see below).

## Adding another add-on (monorepo layout)

This is a standard HA add-on repository — one `repository.yaml` at the root, one
folder per add-on. To add another, drop a new folder next to `nanobot/`:

```text
ha-addons/
  repository.yaml          # repo metadata (shared across all add-ons)
  nanobot/                 # add-on 1 — own config.yaml (unique slug), Dockerfile/image, docs
  <your-addon>/            # add-on 2 — own config.yaml (unique slug), ...
```

Each add-on is fully independent: its own `config.yaml` (slug, version, options,
schema), its own `Dockerfile` or prebuilt `image:`, and its own `DOCS.md`. The HA
store lists every add-on in the repo; nothing here is nanobot-specific.

## Prebuilt images (fast install)

Each add-on can carry an `image:` key so the Supervisor **pulls** a prebuilt
image instead of building on-device. Images are built locally (no CI) and pushed
to GHCR, per add-on and per arch — e.g.
`ghcr.io/niradler/ha-addons-nanobot-{arch}:<version>`. The per-add-on release
steps live in each add-on's README (see
[nanobot/README.md](nanobot/README.md#prebuilt-images-fast-install-no-on-device-build)).

## Development

- Per-add-on unit tests: `python -m pytest tests/ -q` (currently the nanobot
  runtime-config generator).
- Design notes and prior-art comparison: [`docs/`](docs/).
