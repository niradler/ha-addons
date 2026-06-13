"""Unit tests for the add-on's generate-config.py overlay logic.

The artifact under test lives at rootfs/usr/bin/generate-config.py (a hyphenated
filename that isn't importable normally), so we load it via importlib from its
real path — the tests exercise the exact shipped file.
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


# --- deep_merge_fill: user wins, overlay fills gaps --------------------------


def test_deep_merge_fills_missing_keys():
    base = {"a": 1}
    overlay = {"b": 2}
    assert gc.deep_merge_fill(base, overlay) == {"a": 1, "b": 2}


def test_deep_merge_user_scalar_wins():
    base = {"channels": {"websocket": {"enabled": False}}}
    overlay = {"channels": {"websocket": {"enabled": True, "port": 8765}}}
    merged = gc.deep_merge_fill(base, overlay)
    # User explicitly disabled it -> preserved; overlay only fills the new key.
    assert merged["channels"]["websocket"]["enabled"] is False
    assert merged["channels"]["websocket"]["port"] == 8765


def test_deep_merge_does_not_mutate_inputs():
    base = {"tools": {"restrictToWorkspace": False}}
    overlay = {"tools": {"exec": {"enable": True}}}
    gc.deep_merge_fill(base, overlay)
    assert base == {"tools": {"restrictToWorkspace": False}}
    assert "exec" not in overlay["tools"] or overlay["tools"]["exec"] == {"enable": True}


# --- WebUI / Ingress overlay -------------------------------------------------


def test_webui_overlay_binds_for_ingress_no_password_by_default():
    overlay = gc.build_overlay({}, token_issue_secret=SECRET)
    ws = overlay["channels"]["websocket"]
    assert ws["enabled"] is True
    assert ws["host"] == "0.0.0.0"  # so HA Ingress proxy can reach it
    assert ws["port"] == gc.DEFAULT_WEBUI_PORT
    assert ws["websocketRequiresToken"] is False
    # Default: HA SSO is the gate, no nanobot WebUI password.
    assert ws["trustProxyAuth"] is True
    assert "tokenIssueSecret" not in ws


def test_webui_auth_opt_in_uses_token_secret_prompt():
    overlay = gc.build_overlay({"webui_auth": True}, token_issue_secret=SECRET)
    ws = overlay["channels"]["websocket"]
    assert ws["tokenIssueSecret"] == SECRET
    assert "trustProxyAuth" not in ws


# --- Safety knobs ------------------------------------------------------------


def test_restrict_to_workspace_defaults_true():
    overlay = gc.build_overlay({}, token_issue_secret=SECRET)
    assert overlay["tools"]["restrictToWorkspace"] is True


def test_exec_disabled_by_default_and_never_bwrap():
    overlay = gc.build_overlay({}, token_issue_secret=SECRET)
    assert overlay["tools"]["exec"]["enable"] is False
    # We never request the bwrap sandbox (no SYS_ADMIN granted to the add-on).
    assert "sandbox" not in overlay["tools"]["exec"]


def test_exec_can_be_opted_in():
    overlay = gc.build_overlay({"exec_enabled": True}, token_issue_secret=SECRET)
    assert overlay["tools"]["exec"]["enable"] is True


# --- HA MCP wiring + SSRF gating ---------------------------------------------


def test_builtin_mcp_uses_sse_with_token_ref_and_whitelists_ha_cidrs():
    # read_only defaults to True.
    overlay = gc.build_overlay({"ha_mcp_mode": "builtin"}, token_issue_secret=SECRET)
    tools = overlay["tools"]
    server = tools["mcpServers"]["ha_builtin"]
    assert server["type"] == "sse"
    assert server["url"].endswith(gc.HA_MCP_SSE_PATH)
    assert server["headers"]["Authorization"] == "Bearer ${HA_TOKEN}"
    # SSE is SSRF-guarded -> HA-internal CIDRs whitelisted.
    assert tools["ssrfWhitelist"] == gc.HA_INTERNAL_CIDRS


def test_builtin_mcp_read_only_registers_only_read_tool():
    # Default (read-only): only GetLiveContext is exposed, no control intents.
    overlay = gc.build_overlay({"ha_mcp_mode": "builtin"}, token_issue_secret=SECRET)
    server = overlay["tools"]["mcpServers"]["ha_builtin"]
    assert server["enabledTools"] == gc.HA_BUILTIN_READONLY_TOOLS
    assert server["enabledTools"] == ["GetLiveContext"]


def test_builtin_mcp_read_write_drops_allowlist():
    # Explicit opt-out -> no allow-list, all HA tools available ("the cat has claws").
    overlay = gc.build_overlay(
        {"ha_mcp_mode": "builtin", "ha_read_only": False}, token_issue_secret=SECRET
    )
    server = overlay["tools"]["mcpServers"]["ha_builtin"]
    assert "enabledTools" not in server


def test_builtin_mcp_uses_internal_ha_host_by_default():
    overlay = gc.build_overlay({"ha_mcp_mode": "builtin"}, token_issue_secret=SECRET)
    url = overlay["tools"]["mcpServers"]["ha_builtin"]["url"]
    assert url == "http://homeassistant:8123" + gc.HA_MCP_SSE_PATH


def test_builtin_mcp_honors_ha_url_override():
    overlay = gc.build_overlay(
        {"ha_mcp_mode": "builtin", "ha_url": "http://10.0.0.5:8123"},
        token_issue_secret=SECRET,
    )
    url = overlay["tools"]["mcpServers"]["ha_builtin"]["url"]
    assert url == "http://10.0.0.5:8123" + gc.HA_MCP_SSE_PATH


def test_stdio_ha_mcp_is_not_ssrf_whitelisted():
    overlay = gc.build_overlay({"ha_mcp_mode": "ha-mcp"}, token_issue_secret=SECRET)
    tools = overlay["tools"]
    assert "ha_mcp" in tools["mcpServers"]
    assert tools["mcpServers"]["ha_mcp"]["command"]  # stdio transport
    # stdio is SSRF-exempt -> no whitelist widening at all.
    assert "ssrfWhitelist" not in tools


def test_both_modes_register_two_servers_and_whitelist():
    overlay = gc.build_overlay({"ha_mcp_mode": "both"}, token_issue_secret=SECRET)
    servers = overlay["tools"]["mcpServers"]
    assert set(servers) == {"ha_builtin", "ha_mcp"}
    assert overlay["tools"]["ssrfWhitelist"] == gc.HA_INTERNAL_CIDRS


def test_off_mode_registers_no_ha_mcp_and_no_whitelist():
    overlay = gc.build_overlay({"ha_mcp_mode": "off"}, token_issue_secret=SECRET)
    assert "mcpServers" not in overlay["tools"]
    assert "ssrfWhitelist" not in overlay["tools"]


def test_ha_mcp_readonly_tools_subset_applied():
    overlay = gc.build_overlay(
        {"ha_mcp_mode": "ha-mcp", "ha_read_only": True, "ha_mcp_readonly_tools": ["get_state"]},
        token_issue_secret=SECRET,
    )
    assert overlay["tools"]["mcpServers"]["ha_mcp"]["enabledTools"] == ["get_state"]


# --- API binding -------------------------------------------------------------


def test_api_absent_when_disabled():
    overlay = gc.build_overlay({"api_enabled": False}, token_issue_secret=SECRET)
    assert "api" not in overlay


def test_api_internal_bind_when_not_published():
    overlay = gc.build_overlay({"api_enabled": True}, token_issue_secret=SECRET)
    assert overlay["api"]["host"] == "127.0.0.1"
    assert overlay["api"]["port"] == gc.DEFAULT_API_PORT


def test_api_published_binds_all_interfaces():
    overlay = gc.build_overlay(
        {"api_enabled": True, "api_publish_port": True, "api_token": "x", "api_port": 9001},
        token_issue_secret=SECRET,
    )
    assert overlay["api"]["host"] == "0.0.0.0"
    assert overlay["api"]["port"] == 9001


# --- Fail-safe API token rule ------------------------------------------------


def test_failsafe_blocks_publish_without_token():
    errors = gc.validate_failsafe({"api_enabled": True, "api_publish_port": True, "api_token": ""})
    assert errors and "api_token" in errors[0]


def test_failsafe_allows_publish_with_token():
    assert gc.validate_failsafe(
        {"api_enabled": True, "api_publish_port": True, "api_token": "secret"}
    ) == []


def test_failsafe_ignores_internal_api():
    # Not published -> token not required.
    assert gc.validate_failsafe({"api_enabled": True, "api_publish_port": False}) == []


# --- No plaintext secrets in output ------------------------------------------


def test_no_plaintext_secret_in_seed_config():
    options = {
        "ha_mcp_mode": "builtin",
        "ha_token": "super-secret-token-value",
        "llm_provider": "openai",
        "llm_api_key": "sk-super-secret",
    }
    seed = gc.build_seed_config(options, token_issue_secret=SECRET)
    blob = json.dumps(seed)
    assert "super-secret-token-value" not in blob
    assert "sk-super-secret" not in blob
    assert "Bearer ${HA_TOKEN}" in blob
    assert seed["providers"]["openai"]["apiKey"] == "${LLM_API_KEY}"


# --- Seed config -------------------------------------------------------------


def test_seed_provider_only_when_key_present():
    no_key = gc.build_seed_config({"llm_provider": "openai"}, token_issue_secret=SECRET)
    assert "providers" not in no_key
    with_key = gc.build_seed_config(
        {"llm_provider": "openai", "llm_api_key": "x"}, token_issue_secret=SECRET
    )
    assert with_key["providers"]["openai"]["apiKey"] == "${LLM_API_KEY}"


def test_seed_includes_ingress_websocket_and_timezone():
    seed = gc.build_seed_config({"llm_provider": "openai", "llm_api_key": "x"}, token_issue_secret=SECRET)
    assert seed["channels"]["websocket"]["trustProxyAuth"] is True
    assert seed["agents"]["defaults"]["timezone"] == "Asia/Jerusalem"


def test_seed_idempotent_output():
    options = {"ha_mcp_mode": "both", "api_enabled": True, "llm_provider": "openai", "llm_api_key": "x"}
    first = gc.build_seed_config(options, token_issue_secret=SECRET)
    second = gc.build_seed_config(options, token_issue_secret=SECRET)
    assert first == second


# --- token secret persistence ------------------------------------------------


def test_token_secret_persists_across_calls(tmp_path):
    p = tmp_path / "token_issue_secret"
    first = gc.load_or_create_token_secret(p)
    assert first and p.exists()
    second = gc.load_or_create_token_secret(p)
    assert first == second  # stable across boots


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
