#!/usr/bin/env python3
"""Generate nanobot's runtime config from the user's native config + add-on overlay.

Source of truth is the user's native nanobot ``config.json`` (full nanobot
fidelity). The add-on layers a *thin* overlay of only the keys it owns on top of
it, then writes the merged result to a disposable runtime config that nanobot is
launched with (``--config``). The user's authored ``config.json`` is never
rewritten.

Design rules (see docs/2026-06-13-nanobot-ha-addon-design.md §5):

- **User wins.** The overlay only *fills gaps* — any key the user set in their
  own ``config.json`` is preserved (each overlay key is individually skippable).
- **No plaintext secrets on disk.** Secrets arrive as add-on options and are
  exported to the process environment; the overlay only ever references them as
  ``${VAR}``. nanobot resolves the refs in memory at startup.
- **SSRF whitelist is gated.** HA-internal CIDRs are appended *only* when an
  HTTP/SSE HA MCP endpoint is enabled (stdio MCP is SSRF-exempt, so it needs no
  whitelist entry).
- **API publish is fail-safe.** Publishing the OpenAI API on a host port without
  a bearer token is refused (non-zero exit).
- **Idempotent.** Output is a pure function of (user config, options, persisted
  token secret); regenerated from scratch on every boot.

Runs on stock Python 3 (stdlib only) so the unit tests execute on any machine
without installing nanobot or the HA base image.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

# --- Constants ---------------------------------------------------------------

# WebUI is the nanobot ``websocket`` channel; HA Ingress proxies this port.
DEFAULT_WEBUI_PORT = 8765
# OpenAI-compatible API server default (nanobot ApiConfig.port).
DEFAULT_API_PORT = 8900
# HA core MCP Server (the "Model Context Protocol Server" integration) SSE path.
HA_MCP_SSE_PATH = "/mcp_server/sse"
# Reachable HA core base from inside an add-on (hassio DNS name for core, on the
# 172.30.32.0/23 network). We deliberately do NOT use the supervisor proxy /
# ambient SUPERVISOR_TOKEN: the add-on holds no HA credentials of its own, only
# the read-only, user-supplied token the user explicitly hands it.
DEFAULT_HA_BASE_URL = "http://homeassistant:8123"
# HA-internal Docker network ranges to exempt from nanobot's SSRF guard, applied
# ONLY when an HTTP/SSE HA MCP endpoint is enabled.
HA_INTERNAL_CIDRS = ["172.30.32.0/23", "127.0.0.1/32"]
# Read-only allow-list for the built-in HA MCP Server: only the live-context
# read tool is registered, so control intents (HassTurnOn/Off, HassLightSet, …)
# are never exposed to the agent and cannot be called. Fail-safe: an allow-list
# can only *under*-grant, never accidentally permit control.
HA_BUILTIN_READONLY_TOOLS = ["GetLiveContext"]

# Default env var names the overlay references (see ha-token / ha-url options).
ENV_HA_TOKEN = "HA_TOKEN"
ENV_HA_URL = "HA_URL"
ENV_LLM_API_KEY = "LLM_API_KEY"


# --- Pure helpers (unit-tested) ----------------------------------------------


def deep_merge_fill(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Return ``base`` with ``overlay`` values filled in only where missing.

    The user's config (``base``) always wins on scalar conflicts; nested dicts
    merge recursively. This is what makes every overlay key "individually
    skippable if the user set it themselves" and keeps the result idempotent.
    """
    result = dict(base)
    for key, value in overlay.items():
        if key not in result:
            result[key] = value
        elif isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge_fill(result[key], value)
        # else: key present and not both dicts -> user value wins, skip overlay.
    return result


def validate_failsafe(options: dict[str, Any]) -> list[str]:
    """Return a list of fatal config errors (empty = OK).

    Fail-safe rule: publishing the API on a host port REQUIRES a bearer token.
    """
    errors: list[str] = []
    if options.get("api_enabled") and options.get("api_publish_port"):
        if not str(options.get("api_token") or "").strip():
            errors.append(
                "api_publish_port=true requires a non-empty api_token: refusing "
                "to expose the OpenAI API on a host port without a bearer token."
            )
    return errors


def build_overlay(options: dict[str, Any], *, token_issue_secret: str) -> dict[str, Any]:
    """Build the add-on overlay (only add-on-owned keys) from the options."""
    overlay: dict[str, Any] = {}

    # --- WebUI (websocket channel) for HA Ingress ----------------------------
    # Bind 0.0.0.0 so HA's Ingress proxy can reach it over the docker network.
    # The gateway refuses to start on 0.0.0.0 without a token/tokenIssueSecret,
    # so we always supply a generated, persisted tokenIssueSecret.
    # HA Ingress already enforces auth in front of us, so the websocket itself
    # does not require a per-connection token.
    websocket: dict[str, Any] = {
        "enabled": True,
        "host": "0.0.0.0",
        "port": DEFAULT_WEBUI_PORT,
        "path": "/",
        "websocketRequiresToken": False,
        "allowFrom": ["*"],
        "streaming": True,
    }
    if options.get("webui_auth"):
        websocket["tokenIssueSecret"] = token_issue_secret
    else:
        websocket["trustProxyAuth"] = True
    overlay["channels"] = {"websocket": websocket}

    # --- Tool safety ---------------------------------------------------------
    tools: dict[str, Any] = {
        "restrictToWorkspace": bool(options.get("restrict_to_workspace", True)),
        # exec is off by default. We do NOT set sandbox="bwrap" because the
        # add-on is not granted SYS_ADMIN; if the user opts exec on, it runs
        # unsandboxed (documented in DOCS.md).
        "exec": {"enable": bool(options.get("exec_enabled", False))},
    }

    # --- HA control via MCP --------------------------------------------------
    mode = options.get("ha_mcp_mode", "builtin")
    read_only = bool(options.get("ha_read_only", True))
    mcp_servers: dict[str, Any] = {}
    ssrf_whitelist: list[str] = []

    if mode in ("builtin", "both"):
        base_url = str(options.get("ha_url") or options.get("ha_base_url") or DEFAULT_HA_BASE_URL).rstrip("/")
        builtin: dict[str, Any] = {
            "type": "sse",
            "url": base_url + HA_MCP_SSE_PATH,
            # Reference only — the real token is injected via env at startup.
            "headers": {"Authorization": "Bearer ${%s}" % ENV_HA_TOKEN},
        }
        # Read-only safety: register ONLY the read tool, so the agent has no way
        # to invoke control intents. Omit the allow-list only when the user has
        # explicitly turned read-only off.
        if read_only:
            builtin["enabledTools"] = list(HA_BUILTIN_READONLY_TOOLS)
        mcp_servers["ha_builtin"] = builtin
        # SSE = SSRF-guarded transport -> whitelist HA-internal ranges (gated).
        ssrf_whitelist = list(HA_INTERNAL_CIDRS)

    if mode in ("ha-mcp", "both"):
        # stdio transport (SSRF-exempt). The concrete ha-mcp implementation /
        # version is pinned later (design §8); command/args are configurable.
        server: dict[str, Any] = {
            "command": str(options.get("ha_mcp_command") or "ha-mcp"),
            "args": list(options.get("ha_mcp_args") or []),
            "env": {
                ENV_HA_URL: "${%s}" % ENV_HA_URL,
                ENV_HA_TOKEN: "${%s}" % ENV_HA_TOKEN,
            },
        }
        # Read-only is expressed as an enabledTools subset when the user supplies
        # one (the exact tool names depend on the pinned implementation).
        ro_tools = options.get("ha_mcp_readonly_tools")
        if read_only and ro_tools:
            server["enabledTools"] = list(ro_tools)
        mcp_servers["ha_mcp"] = server

    if mcp_servers:
        tools["mcpServers"] = mcp_servers
    if ssrf_whitelist:
        tools["ssrfWhitelist"] = ssrf_whitelist

    overlay["tools"] = tools

    # --- OpenAI-compatible API binding --------------------------------------
    if options.get("api_enabled"):
        overlay["api"] = {
            "host": "0.0.0.0" if options.get("api_publish_port") else "127.0.0.1",
            "port": int(options.get("api_port") or DEFAULT_API_PORT),
        }

    return overlay


def build_seed_config(options: dict[str, Any], *, token_issue_secret: str) -> dict[str, Any]:
    """Full nanobot config written once on first run; nanobot's WebUI owns it after."""
    cfg = build_overlay(options, token_issue_secret=token_issue_secret)
    defaults: dict[str, Any] = {"timezone": "Asia/Jerusalem"}
    provider = options.get("llm_provider")
    model = options.get("llm_model")
    if model:
        defaults["model"] = model
    if provider:
        defaults["provider"] = provider
    cfg["agents"] = {"defaults": defaults}
    if options.get("llm_api_key") and provider:
        cfg["providers"] = {provider: {"apiKey": "${%s}" % ENV_LLM_API_KEY}}
    return cfg


# --- IO glue (not unit-tested directly) --------------------------------------


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_or_create_token_secret(path: Path) -> str:
    """Read the persisted WebUI token-issue secret, generating one on first run."""
    if path.exists():
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    secret = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(secret, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass  # best-effort on filesystems without POSIX perms
    return secret


def main() -> int:
    options_path = Path(os.environ.get("NANOBOT_OPTIONS", "/data/options.json"))
    config_path = Path(os.environ.get("NANOBOT_CONFIG", "/config/nanobot/settings/config.json"))
    secret_path = Path(os.environ.get("NANOBOT_TOKEN_SECRET", "/data/token_issue_secret"))

    options = _load_json(options_path, {})

    errors = validate_failsafe(options)
    if errors:
        for err in errors:
            print(f"[generate-config] FATAL: {err}", file=sys.stderr)
        return 1

    if config_path.exists():
        print(f"[generate-config] {config_path} exists — leaving it to nanobot", file=sys.stderr)
        return 0

    token_secret = load_or_create_token_secret(secret_path)
    seed = build_seed_config(options, token_issue_secret=token_secret)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(seed, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[generate-config] seeded {config_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
