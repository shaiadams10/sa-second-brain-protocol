from __future__ import annotations

import json
from pathlib import Path

import pytest

from second_brain_protocol.config import (
    RuntimePaths,
    default_runtime_config,
    load_defaults,
    load_runtime_config,
    set_model_runtime,
    setup_runtime,
)


def test_openrouter_hybrid_policy_resolves_locked_roles() -> None:
    defaults = load_defaults({"model_policy": "openrouter-hybrid-v1"})

    assert defaults["active_model_policy"] == "openrouter-hybrid-v1"
    assert defaults["models"] == {
        "daily": {
            "name": "deepseek/deepseek-v4-flash",
            "reasoning": "high",
        },
        "weekly": {"name": "openai/gpt-5.6-sol", "reasoning": "high"},
        "bootstrap": {"name": "openai/gpt-5.6-sol", "reasoning": "high"},
        "escalation": {"name": "openai/gpt-5.6-sol", "reasoning": "high"},
    }


def test_unknown_model_policy_fails_closed() -> None:
    with pytest.raises(RuntimeError, match="Unknown model policy"):
        load_defaults({"model_policy": "unapproved-policy"})


def test_openrouter_provider_writes_isolated_codex_configuration(
    tmp_path: Path,
) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    config = default_runtime_config(paths)
    config["model_provider"] = "openrouter"
    config["model_policy"] = "openrouter-hybrid-v1"
    paths.root.mkdir(parents=True)
    paths.config.write_text(json.dumps(config), encoding="utf-8")

    setup_runtime(paths)

    codex_config = (paths.codex_home / "config.toml").read_text(encoding="utf-8")
    assert 'model_provider = "openrouter"' in codex_config
    assert 'base_url = "https://openrouter.ai/api/v1"' in codex_config
    assert 'wire_api = "responses"' in codex_config
    assert "openrouter-runtime.dpapi" in codex_config
    assert "ConvertTo-SecureString" in codex_config
    assert "$env:OPENROUTER_API_KEY" not in codex_config


def test_default_runtime_remains_on_direct_provider(tmp_path: Path) -> None:
    config = default_runtime_config(RuntimePaths.from_root(tmp_path / "runtime"))

    assert config["model_provider"] == "openai"
    assert config["model_policy"] == "chatgpt-direct-v1"


def test_model_runtime_switch_is_persisted_and_regenerates_codex_config(
    tmp_path: Path,
) -> None:
    paths = RuntimePaths.from_root(tmp_path / "runtime")
    setup_runtime(paths)

    selected = set_model_runtime(
        "openrouter",
        "openrouter-hybrid-v1",
        paths,
    )

    assert selected == {
        "model_provider": "openrouter",
        "model_policy": "openrouter-hybrid-v1",
    }
    assert load_runtime_config(paths)["model_provider"] == "openrouter"
    assert load_runtime_config(paths)["model_policy"] == "openrouter-hybrid-v1"
    assert 'model_provider = "openrouter"' in (
        paths.codex_home / "config.toml"
    ).read_text(encoding="utf-8")
