# Changelog

## 0.1.0

- Initial add-on: nanobot built from the `niradler/nanobot` fork (`ha` branch).
- WebUI via HA Ingress, OpenAI-compatible API, HA control via MCP (builtin
  SSE and/or stdio ha-mcp), chat channels + MQTT.
- Native `config.json` as source of truth + thin runtime overlay.
- Secret handling via add-on options → `${VAR}` env refs (never on disk).
- Fail-safe API publish (requires bearer token).
