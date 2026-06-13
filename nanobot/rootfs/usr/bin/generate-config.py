#!/usr/bin/env python3
"""Seed nanobot's config on first run; the nanobot WebUI owns it afterwards.

The add-on only writes nanobot's config once, with the bits it must own (Ingress
wiring, secure defaults, secret references). Providers, MCP servers (including HA
control), tools, and channels are configured in the nanobot WebUI or by editing
config.json. Stdlib only, so the unit tests run anywhere.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

DEFAULT_WEBUI_PORT = 8765
DEFAULT_API_PORT = 8900
ENV_LLM_API_KEY = "LLM_API_KEY"


def validate_failsafe(options: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if options.get("api_enabled") and options.get("api_publish_port"):
        if not str(options.get("api_token") or "").strip():
            errors.append(
                "api_publish_port=true requires a non-empty api_token: refusing "
                "to expose the OpenAI API on a host port without a bearer token."
            )
    return errors


def build_overlay(options: dict[str, Any], *, token_issue_secret: str) -> dict[str, Any]:
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

    overlay: dict[str, Any] = {
        "channels": {"websocket": websocket},
        "tools": {"restrictToWorkspace": True, "exec": {"enable": False}},
    }
    if options.get("api_enabled"):
        overlay["api"] = {
            "host": "0.0.0.0" if options.get("api_publish_port") else "127.0.0.1",
            "port": int(options.get("api_port") or DEFAULT_API_PORT),
        }
    return overlay


def build_seed_config(options: dict[str, Any], *, token_issue_secret: str) -> dict[str, Any]:
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


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def load_or_create_token_secret(path: Path) -> str:
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
        pass
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
