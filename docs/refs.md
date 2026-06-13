# References

Prior art reviewed while building this add-on, and how our design compares.

- <https://github.com/Sangoku/HomeAsistantNanoBot>
- <https://github.com/Good0007/nanobot-webui>
- <https://github.com/dartanidi/ha-nanobot>
- <https://github.com/licheng5625/nanobot-hass>

## What each one is

| Project | Type | WebUI access | HA control | Read-only |
|---|---|---|---|---|
| **Sangoku/HomeAsistantNanoBot** | HA add-on (Python wrapper around nanobot) | **Host port 18780** + own login (`admin/nanobot`); `ingress: false` | ha-mcp | 45-tool whitelist ("On=safe, Off=claws") |
| **Good0007/nanobot-webui** | Standalone WebUI panel (not an add-on) | Host port 18780, JWT, `admin/nanobot` | — (UI only) | — |
| **dartanidi/ha-nanobot** | HA add-on | Unspecified in docs; workspace in `/share`; MCP + local RAG | MCP | unspecified |
| **licheng5625/nanobot-hass** | HA **custom component** (integration) | none (registers as Assist conversation agent) | REST + long-lived token, **read/write** | no (full control) |
| **Ours** | HA add-on, nanobot built from `niradler/nanobot@ha` | **HA Ingress** (HA SSO, no open port) | built-in HA MCP and/or ha-mcp | allow-list (`GetLiveContext`) + HA control **off** by default |

## Key takeaways

- **Ingress base-path is unsolved upstream.** None of these serve nanobot's
  native WebUI through HA Ingress — they sidestep it with a host port + app-level
  login, or are a different integration type. Our fork `ha`-branch fix (relative
  `vite` base + base-path-aware REST/WS) is what makes the native WebUI work
  behind Ingress. (This is the reason we fork.)
- **Ours is the most secure of the set:** HA SSO (no separate password), no open
  port, **no ambient HA credentials** (no `homeassistant_api` grant), and
  read-only HA control off-by-default with a tool allow-list.
- **Config philosophy:** ours keeps the native nanobot `config.json` as the
  source of truth + a thin runtime overlay, vs. others that generate config from
  add-on options.

## Features we deferred (covered by a reference, out of scope for our v1)

- **Conversation-agent / Assist voice registration** — `licheng5625/nanobot-hass`
  does this as a custom component (WS to `ws://ha:8123/api/websocket`). Ours
  exposes the OpenAI API; register with Assist manually for now (design §12).
- **HA event-bus listener** (real-time state stream) — Sangoku's custom script;
  not in our v1.
- **Local RAG** — mentioned by `dartanidi/ha-nanobot`; not in scope.
