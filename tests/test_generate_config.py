"""Unit tests for the add-on's generate-config.py seed logic.

Loads the shipped rootfs/usr/bin/generate-config.py via importlib (hyphenated
filename), so the tests exercise the exact artifact.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_GEN = (
    Path(__file__).resolve().parent.parent
    / "nanobot"
    / "rootfs"
    / "usr"
    / "bin"
    / "generate-config.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("nanobot_generate_config", _GEN)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gc = _load_module()

SECRET = "test-token-issue-secret"


# --- WebUI / Ingress ---------------------------------------------------------


def test_webui_binds_for_ingress_no_password_by_default():
    overlay = gc.build_overlay({}, token_issue_secret=SECRET)
    ws = overlay["channels"]["websocket"]
    assert ws["enabled"] is True
    assert ws["host"] == "0.0.0.0"
    assert ws["port"] == gc.DEFAULT_WEBUI_PORT
    assert ws["websocketRequiresToken"] is False
    assert ws["trustProxyAuth"] is True
    assert "tokenIssueSecret" not in ws


def test_webui_auth_opt_in_uses_token_secret():
    ws = gc.build_overlay({"webui_auth": True}, token_issue_secret=SECRET)["channels"]["websocket"]
    assert ws["tokenIssueSecret"] == SECRET
    assert "trustProxyAuth" not in ws


# --- Secure tool defaults (seeded, not options) ------------------------------


def test_secure_tool_defaults_seeded():
    tools = gc.build_overlay({}, token_issue_secret=SECRET)["tools"]
    assert tools["restrictToWorkspace"] is True
    assert tools["exec"]["enable"] is False
    assert "sandbox" not in tools["exec"]


def test_overlay_does_not_seed_ha_mcp():
    # HA MCP is configured in the WebUI / config.json, not by the add-on.
    overlay = gc.build_overlay({}, token_issue_secret=SECRET)
    assert "mcpServers" not in overlay["tools"]
    assert "ssrfWhitelist" not in overlay["tools"]


# --- API binding -------------------------------------------------------------


def test_api_absent_when_disabled():
    assert "api" not in gc.build_overlay({"api_enabled": False}, token_issue_secret=SECRET)


def test_api_internal_bind_when_not_published():
    api = gc.build_overlay({"api_enabled": True}, token_issue_secret=SECRET)["api"]
    assert api["host"] == "127.0.0.1"
    assert api["port"] == gc.DEFAULT_API_PORT


def test_api_published_binds_all_interfaces():
    api = gc.build_overlay(
        {"api_enabled": True, "api_publish_port": True, "api_token": "x", "api_port": 9001},
        token_issue_secret=SECRET,
    )["api"]
    assert api["host"] == "0.0.0.0"
    assert api["port"] == 9001


# --- Fail-safe API token rule ------------------------------------------------


def test_failsafe_blocks_publish_without_token():
    errors = gc.validate_failsafe({"api_enabled": True, "api_publish_port": True, "api_token": ""})
    assert errors and "api_token" in errors[0]


def test_failsafe_allows_publish_with_token():
    assert gc.validate_failsafe(
        {"api_enabled": True, "api_publish_port": True, "api_token": "secret"}
    ) == []


def test_failsafe_ignores_internal_api():
    assert gc.validate_failsafe({"api_enabled": True, "api_publish_port": False}) == []


# --- Seed config -------------------------------------------------------------


def test_seed_provider_only_when_key_present():
    no_key = gc.build_seed_config({"llm_provider": "openai"}, token_issue_secret=SECRET)
    assert "providers" not in no_key
    with_key = gc.build_seed_config(
        {"llm_provider": "openai", "llm_api_key": "x"}, token_issue_secret=SECRET
    )
    assert with_key["providers"]["openai"]["apiKey"] == "${LLM_API_KEY}"


def test_seed_includes_ingress_and_timezone():
    seed = gc.build_seed_config(
        {"llm_provider": "openai", "llm_api_key": "x", "llm_model": "gpt-4.1"},
        token_issue_secret=SECRET,
    )
    assert seed["channels"]["websocket"]["trustProxyAuth"] is True
    assert seed["agents"]["defaults"]["timezone"] == "Asia/Jerusalem"
    assert seed["agents"]["defaults"]["model"] == "gpt-4.1"


def test_seed_no_plaintext_secret():
    seed = gc.build_seed_config(
        {"llm_provider": "openai", "llm_api_key": "sk-super-secret"}, token_issue_secret=SECRET
    )
    assert "sk-super-secret" not in json.dumps(seed)
    assert seed["providers"]["openai"]["apiKey"] == "${LLM_API_KEY}"


def test_seed_idempotent():
    options = {"llm_provider": "openai", "llm_api_key": "x", "api_enabled": True}
    a = gc.build_seed_config(options, token_issue_secret=SECRET)
    b = gc.build_seed_config(options, token_issue_secret=SECRET)
    assert a == b


# --- token secret persistence ------------------------------------------------


def test_token_secret_persists(tmp_path):
    p = tmp_path / "token_issue_secret"
    first = gc.load_or_create_token_secret(p)
    assert first and p.exists()
    assert gc.load_or_create_token_secret(p) == first


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
