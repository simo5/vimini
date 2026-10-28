import json
import sys
import os
import pytest
from unittest.mock import patch, mock_open

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini.common.config import (
    DEFAULT_BACKEND,
    DEFAULT_MODEL,
    DEFAULT_GEMINI_KEY_FILE,
    DEFAULT_UI_CONFIG,
    create_default_global_config,
    validate_global_config,
    load_global_config,
    save_global_config,
    get_active_backend,
    get_active_backend_config,
    set_active_backend,
    load_backend_api_key,
    backend_requires_api_key,
)


def test_create_default_global_config():
    cfg = create_default_global_config()
    assert cfg["active_backend"] == DEFAULT_BACKEND
    assert DEFAULT_BACKEND in cfg["backends"]
    assert "ui" in cfg
    assert cfg["ui"]["thinking_color"] == DEFAULT_UI_CONFIG["thinking_color"]
    gemini = cfg["backends"][DEFAULT_BACKEND]
    assert gemini["api"] == "genai"
    assert gemini["api_key_file"] == DEFAULT_GEMINI_KEY_FILE
    assert gemini["default_model"] == DEFAULT_MODEL


def test_create_default_global_config_custom_args():
    cfg = create_default_global_config(
        default_model="custom-model", api_key_file="/path/to/key"
    )
    assert cfg["backends"][DEFAULT_BACKEND]["default_model"] == "custom-model"
    assert cfg["backends"][DEFAULT_BACKEND]["api_key_file"] == "/path/to/key"


def test_load_global_config_creates_default_when_missing(tmp_path):
    config_file = tmp_path / "config.json"
    assert not config_file.exists()

    cfg = load_global_config(config_path=str(config_file), default_model="gemini-test")
    assert config_file.exists()
    assert cfg["active_backend"] == "Gemini"
    assert cfg["backends"]["Gemini"]["default_model"] == "gemini-test"

    # Verify content written to disk
    with open(config_file, "r", encoding="utf-8") as f:
        disk_data = json.load(f)
    assert disk_data == cfg


def test_load_global_config_reads_existing(tmp_path):
    config_file = tmp_path / "config.json"
    data = {
        "active_backend": "Custom 1",
        "backends": {
            "Gemini": {
                "api": "genai",
                "api_key_file": "~/.config/gemini.token",
                "default_model": "gemini-3.8-flash",
            },
            "Custom 1": {
                "api": "openai",
                "base_url": "https://api.proxy.com/v1",
                "api_key_file": "~/.config/proxyapi.token",
                "default_model": "gpt-4o",
            },
        },
    }
    config_file.write_text(json.dumps(data), encoding="utf-8")

    cfg = load_global_config(config_path=str(config_file))
    assert cfg["active_backend"] == "Custom 1"
    assert "Custom 1" in cfg["backends"]


def test_load_global_config_invalid_json(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text("invalid json {{{", encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to parse global configuration"):
        load_global_config(config_path=str(config_file))


def test_validate_global_config_errors():
    with pytest.raises(ValueError, match="must be a dictionary"):
        validate_global_config(["not a dict"])

    with pytest.raises(ValueError, match="non-empty 'active_backend'"):
        validate_global_config({"active_backend": "", "backends": {}})

    with pytest.raises(ValueError, match="non-empty 'backends' dictionary"):
        validate_global_config({"active_backend": "Gemini", "backends": {}})

    with pytest.raises(ValueError, match="not defined in 'backends'"):
        validate_global_config(
            {
                "active_backend": "Unknown",
                "backends": {"Gemini": {"api": "genai"}},
            }
        )

    with pytest.raises(ValueError, match="must be a dictionary"):
        validate_global_config(
            {
                "active_backend": "Gemini",
                "backends": {"Gemini": "not a dict"},
            }
        )

    with pytest.raises(ValueError, match="must specify a non-empty 'api' type"):
        validate_global_config(
            {
                "active_backend": "Gemini",
                "backends": {"Gemini": {"api": ""}},
            }
        )

    with pytest.raises(ValueError, match="api 'anthropic' is not supported"):
        validate_global_config(
            {
                "active_backend": "Anthropic",
                "backends": {
                    "Anthropic": {
                        "api": "anthropic",
                        "api_key_file": "~/.config/anthropic.token",
                    }
                },
            }
        )

    with pytest.raises(ValueError, match="must specify a non-empty 'api_key_file'"):
        validate_global_config(
            {
                "active_backend": "Gemini",
                "backends": {"Gemini": {"api": "genai"}},
            }
        )

    validate_global_config(
        {
            "active_backend": "Local",
            "backends": {
                "Local": {
                    "api": "openai",
                    "base_url": "http://localhost:11434/v1",
                    "default_model": "llama3",
                }
            },
        }
    )

    with pytest.raises(ValueError, match="must specify a non-empty 'default_model'"):
        validate_global_config(
            {
                "active_backend": "Local",
                "backends": {
                    "Local": {
                        "api": "openai",
                        "base_url": "http://localhost:11434/v1",
                    }
                },
            }
        )

    with pytest.raises(ValueError, match="must specify a non-empty 'api_key_file'"):
        validate_global_config(
            {
                "active_backend": "Gemini",
                "backends": {"Gemini": {"api": "genai", "api_key_file": ""}},
            }
        )

    with pytest.raises(ValueError, match="'api_key_file' must be a string path"):
        validate_global_config(
            {
                "active_backend": "Gemini",
                "backends": {"Gemini": {"api": "genai", "api_key_file": 123}},
            }
        )

    # UI preference validation
    with pytest.raises(ValueError, match="'ui' must be a dictionary"):
        validate_global_config(
            {
                "active_backend": "Gemini",
                "backends": {"Gemini": {"api": "genai", "api_key_file": "~/.token"}},
                "ui": "invalid",
            }
        )

    with pytest.raises(ValueError, match="'ui.thinking_color' must be a string"):
        validate_global_config(
            {
                "active_backend": "Gemini",
                "backends": {"Gemini": {"api": "genai", "api_key_file": "~/.token"}},
                "ui": {"thinking_color": 123},
            }
        )


def test_save_global_config(tmp_path):
    config_file = tmp_path / "subdir" / "config.json"
    data = create_default_global_config()

    res = save_global_config(data, config_path=str(config_file))
    assert res is True
    assert config_file.exists()

    with open(config_file, "r", encoding="utf-8") as f:
        saved = json.load(f)
    assert saved == data


def test_get_active_backend():
    assert get_active_backend({"active_backend": "MyBackend"}) == "MyBackend"
    assert get_active_backend({}) == DEFAULT_BACKEND
    assert get_active_backend(None) == DEFAULT_BACKEND


def test_get_active_backend_config():
    cfg = {
        "active_backend": "Proxy",
        "backends": {
            "Proxy": {
                "api": "openai",
                "api_key_file": "~/.config/proxy.token",
                "default_model": "gpt-4o",
            },
            "Gemini": {"api": "genai", "api_key_file": "~/.config/gemini.token"},
        },
    }
    name, b_cfg = get_active_backend_config(cfg)
    assert name == "Proxy"
    assert b_cfg["api"] == "openai"

    name, b_cfg = get_active_backend_config(None)
    assert name is None
    assert b_cfg is None


def test_set_active_backend(tmp_path):
    config_file = tmp_path / "config.json"
    data = {
        "active_backend": "Gemini",
        "backends": {
            "Gemini": {"api": "genai", "api_key_file": "~/.config/gemini.token"},
            "Custom 1": {
                "api": "openai",
                "api_key_file": "~/.config/custom.token",
                "default_model": "gpt-compatible",
            },
        },
    }
    save_global_config(data, config_path=str(config_file))

    updated = set_active_backend("Custom 1", config_path=str(config_file))
    assert updated["active_backend"] == "Custom 1"

    # Verify persisted
    reloaded = load_global_config(config_path=str(config_file))
    assert reloaded["active_backend"] == "Custom 1"


def test_set_active_backend_unknown(tmp_path):
    config_file = tmp_path / "config.json"
    data = create_default_global_config()
    save_global_config(data, config_path=str(config_file))

    with pytest.raises(ValueError, match="Backend 'NonExistent' not found"):
        set_active_backend("NonExistent", config_path=str(config_file))


def test_load_backend_api_key(tmp_path):
    key_file = tmp_path / "key.token"
    key_file.write_text("  my-secret-key-123\n", encoding="utf-8")

    backend_cfg = {"api": "openai", "api_key_file": str(key_file)}
    key = load_backend_api_key(backend_cfg)
    assert key == "my-secret-key-123"


def test_load_backend_api_key_empty_or_missing(tmp_path):
    assert load_backend_api_key({}) is None
    assert load_backend_api_key({"api_key_file": None}) is None

    missing_file = tmp_path / "nonexistent.token"
    assert load_backend_api_key({"api_key_file": str(missing_file)}) is None

    empty_file = tmp_path / "empty.token"
    empty_file.write_text("   \n", encoding="utf-8")
    assert load_backend_api_key({"api_key_file": str(empty_file)}) is None


def test_backend_requires_api_key():
    # Non-dict defaults to True
    assert backend_requires_api_key(None) is True
    assert backend_requires_api_key("string") is True

    # GenAI backends require API key by default
    assert backend_requires_api_key({"api": "genai"}) is True
    assert (
        backend_requires_api_key({"api": "genai", "api_key_file": "~/.token"}) is True
    )
    assert (
        backend_requires_api_key({"api": "genai", "requires_api_key": False}) is False
    )

    # OpenAI without api_key_file defaults to False
    assert backend_requires_api_key({"api": "openai"}) is False
    assert (
        backend_requires_api_key(
            {"api": "openai", "base_url": "http://localhost:11434"}
        )
        is False
    )

    # OpenAI with api_key_file defaults to True
    assert (
        backend_requires_api_key(
            {"api": "openai", "api_key_file": "~/.config/openai.token"}
        )
        is True
    )
    assert backend_requires_api_key({"api": "openai", "api_key_file": ""}) is False

    # Explicit requires_api_key overrides default
    assert (
        backend_requires_api_key(
            {
                "api": "openai",
                "api_key_file": "~/.config/openai.token",
                "requires_api_key": False,
            }
        )
        is False
    )
    assert backend_requires_api_key({"api": "openai", "requires_api_key": True}) is True
    assert (
        backend_requires_api_key({"api": "openai", "requires_api_key": "yes"}) is True
    )
    assert backend_requires_api_key({"api": "openai", "requires_api_key": "0"}) is False
