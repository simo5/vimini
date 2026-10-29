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
    normalize_global_config_base_urls,
    strip_api_version,
    validate_global_config,
    get_global_config,
    set_global_config,
    load_global_config,
    save_global_config,
    get_default_backend,
    get_default_backend_config,
    set_default_backend,
    load_backend_api_key,
    backend_requires_api_key,
)


def test_create_default_global_config():
    cfg = create_default_global_config()
    assert cfg["default_backend"] == DEFAULT_BACKEND
    assert "active_backend" not in cfg
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
    assert cfg["default_backend"] == "Gemini"
    assert "active_backend" not in cfg
    assert cfg["backends"]["Gemini"]["default_model"] == "gemini-test"

    # Verify content written to disk
    with open(config_file, "r", encoding="utf-8") as f:
        disk_data = json.load(f)
    assert disk_data == cfg


def test_load_global_config_reads_existing(tmp_path):
    config_file = tmp_path / "config.json"
    data = {
        "default_backend": "Custom 1",
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
    assert cfg["default_backend"] == "Custom 1"
    assert "Custom 1" in cfg["backends"]
    # Legacy '/v1' suffixes are stripped from stored base URLs.
    assert cfg["backends"]["Custom 1"]["base_url"] == "https://api.proxy.com"

    # The normalized configuration is written back to disk.
    with open(config_file, "r", encoding="utf-8") as f:
        disk_data = json.load(f)
    assert disk_data["backends"]["Custom 1"]["base_url"] == "https://api.proxy.com"


def test_load_global_config_preserves_and_ignores_legacy_active_backend(tmp_path):
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
                "base_url": "https://api.proxy.com",
                "api_key_file": "~/.config/proxyapi.token",
                "default_model": "gpt-4o",
            },
        },
    }
    config_file.write_text(json.dumps(data), encoding="utf-8")

    cfg = load_global_config(config_path=str(config_file))
    # active_backend is not removed, but is ignored when resolving the default backend
    assert cfg["active_backend"] == "Custom 1"
    assert get_default_backend(cfg) == "Gemini"


def test_strip_api_version():
    assert strip_api_version("https://api.openai.com/v1") == "https://api.openai.com"
    assert strip_api_version("https://api.openai.com/v1/") == "https://api.openai.com"
    assert strip_api_version("http://localhost:11434") == "http://localhost:11434"
    assert strip_api_version("http://localhost:11434/") == "http://localhost:11434"
    assert strip_api_version("https://host/api/v1") == "https://host/api"
    # Only the configured version suffix is stripped.
    assert strip_api_version("https://host/v1beta") == "https://host/v1beta"
    assert strip_api_version("") == ""
    assert strip_api_version(None) is None


def test_normalize_global_config_base_urls():
    config = {
        "default_backend": "Proxy",
        "backends": {
            "Gemini": {"api": "genai", "base_url": "https://genai.example/v1"},
            "Proxy": {"api": "openai", "base_url": "https://proxy.example/v1"},
            "Claude": {"api": "anthropic", "base_url": "https://claude.example/v1/"},
            "Local": {"api": "openai", "base_url": "http://localhost:11434"},
        },
    }

    assert normalize_global_config_base_urls(config) is True
    backends = config["backends"]
    assert backends["Proxy"]["base_url"] == "https://proxy.example"
    assert backends["Claude"]["base_url"] == "https://claude.example"
    assert backends["Local"]["base_url"] == "http://localhost:11434"
    # Non-versioned APIs are left untouched.
    assert backends["Gemini"]["base_url"] == "https://genai.example/v1"

    # Already normalized configurations report no changes.
    assert normalize_global_config_base_urls(config) is False


def test_load_global_config_invalid_json(tmp_path):
    config_file = tmp_path / "config.json"
    config_file.write_text("invalid json {{{", encoding="utf-8")

    with pytest.raises(ValueError, match="Failed to parse global configuration"):
        load_global_config(config_path=str(config_file))


def test_validate_global_config_errors():
    with pytest.raises(ValueError, match="must be a dictionary"):
        validate_global_config(["not a dict"])

    with pytest.raises(ValueError, match="non-empty 'backends' dictionary"):
        validate_global_config({"default_backend": "Gemini", "backends": {}})

    with pytest.raises(ValueError, match="non-empty 'default_backend'"):
        validate_global_config(
            {
                "default_backend": "",
                "backends": {
                    "Gemini": {"api": "genai", "api_key_file": "~/.config/gemini.token"}
                },
            }
        )

    with pytest.raises(ValueError, match="not defined in 'backends'"):
        validate_global_config(
            {
                "default_backend": "Unknown",
                "backends": {"Gemini": {"api": "genai"}},
            }
        )

    # Legacy active_backend in config is ignored and not removed
    legacy_cfg = {
        "default_backend": "Gemini",
        "active_backend": "StaleBackend",
        "backends": {
            "Gemini": {"api": "genai", "api_key_file": "~/.config/gemini.token"}
        },
    }
    validated = validate_global_config(legacy_cfg)
    assert validated["active_backend"] == "StaleBackend"

    with pytest.raises(ValueError, match="must be a dictionary"):
        validate_global_config(
            {
                "default_backend": "Gemini",
                "backends": {"Gemini": "not a dict"},
            }
        )

    with pytest.raises(ValueError, match="must specify a non-empty 'api' type"):
        validate_global_config(
            {
                "default_backend": "Gemini",
                "backends": {"Gemini": {"api": ""}},
            }
        )

    with pytest.raises(ValueError, match="api 'unsupported' is not supported"):
        validate_global_config(
            {
                "default_backend": "Unsupported",
                "backends": {
                    "Unsupported": {
                        "api": "unsupported",
                        "api_key_file": "~/.config/anthropic.token",
                    }
                },
            }
        )

    with pytest.raises(ValueError, match="must specify a non-empty 'api_key_file'"):
        validate_global_config(
            {
                "default_backend": "Claude",
                "backends": {
                    "Claude": {
                        "api": "anthropic",
                        "default_model": "claude-3-7-sonnet-20250219",
                    }
                },
            }
        )

    with pytest.raises(ValueError, match="must specify a non-empty 'default_model'"):
        validate_global_config(
            {
                "default_backend": "Claude",
                "backends": {
                    "Claude": {
                        "api": "anthropic",
                        "api_key_file": "~/.config/anthropic.token",
                    }
                },
            }
        )

    validate_global_config(
        {
            "default_backend": "Claude",
            "backends": {
                "Claude": {
                    "api": "anthropic",
                    "api_key_file": "~/.config/anthropic.token",
                    "default_model": "claude-3-7-sonnet-20250219",
                }
            },
        }
    )

    with pytest.raises(ValueError, match="'max_tokens' must be a positive integer"):
        validate_global_config(
            {
                "default_backend": "Claude",
                "backends": {
                    "Claude": {
                        "api": "anthropic",
                        "api_key_file": "~/.config/anthropic.token",
                        "default_model": "claude-3-7-sonnet-20250219",
                        "max_tokens": "not-an-int",
                    }
                },
            }
        )

    with pytest.raises(
        ValueError,
        match="'thinking_budget_tokens' \\(512\\) must be at least 1024",
    ):
        validate_global_config(
            {
                "default_backend": "Claude",
                "backends": {
                    "Claude": {
                        "api": "anthropic",
                        "api_key_file": "~/.config/anthropic.token",
                        "default_model": "claude-3-7-sonnet-20250219",
                        "thinking_budget_tokens": 512,
                    }
                },
            }
        )

    with pytest.raises(
        ValueError,
        match="'thinking_budget_tokens' \\(4096\\) must be less than 'max_tokens' \\(4096\\)",
    ):
        validate_global_config(
            {
                "default_backend": "Claude",
                "backends": {
                    "Claude": {
                        "api": "anthropic",
                        "api_key_file": "~/.config/anthropic.token",
                        "default_model": "claude-3-7-sonnet-20250219",
                        "thinking_budget_tokens": 4096,
                    }
                },
            }
        )

    with pytest.raises(ValueError, match="must specify a non-empty 'api_key_file'"):
        validate_global_config(
            {
                "default_backend": "Gemini",
                "backends": {"Gemini": {"api": "genai"}},
            }
        )

    validate_global_config(
        {
            "default_backend": "Local",
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
                "default_backend": "Local",
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
                "default_backend": "Gemini",
                "backends": {"Gemini": {"api": "genai", "api_key_file": ""}},
            }
        )

    with pytest.raises(ValueError, match="'api_key_file' must be a string path"):
        validate_global_config(
            {
                "default_backend": "Gemini",
                "backends": {"Gemini": {"api": "genai", "api_key_file": 123}},
            }
        )

    # UI preference validation
    with pytest.raises(ValueError, match="'ui' must be a dictionary"):
        validate_global_config(
            {
                "default_backend": "Gemini",
                "backends": {"Gemini": {"api": "genai", "api_key_file": "~/.token"}},
                "ui": "invalid",
            }
        )

    with pytest.raises(ValueError, match="'ui.thinking_color' must be a string"):
        validate_global_config(
            {
                "default_backend": "Gemini",
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


def test_get_global_config_reads_file_once_and_uses_memory_variable(tmp_path):
    from vimini.common import config as common_config

    config_file = tmp_path / "config.json"
    data = create_default_global_config(default_model="cached-model")
    config_file.write_text(json.dumps(data), encoding="utf-8")

    with (
        patch.object(common_config, "DEFAULT_CONFIG_PATH", str(config_file)),
        patch.object(common_config, "_GLOBAL_CONFIG", None),
    ):
        first = get_global_config()
        assert first["backends"]["Gemini"]["default_model"] == "cached-model"
        assert common_config._GLOBAL_CONFIG is first

        # Modify file on disk; subsequent get_global_config() calls must return
        # the in-memory global variable without re-reading the file.
        config_file.write_text("invalid json {{{", encoding="utf-8")
        second = get_global_config()
        assert second is first


def test_get_default_backend():
    assert get_default_backend({"default_backend": "MyBackend"}) == "MyBackend"
    # Ignores active_backend
    assert (
        get_default_backend(
            {
                "active_backend": "Ignored",
                "default_backend": "ConfiguredDefault",
            }
        )
        == "ConfiguredDefault"
    )
    assert (
        get_default_backend(
            {
                "active_backend": "Ignored",
                "backends": {"Gemini": {}, "Ignored": {}},
            }
        )
        == "Gemini"
    )
    assert get_default_backend({}) == DEFAULT_BACKEND
    assert get_default_backend(None) == DEFAULT_BACKEND


def test_get_default_backend_config():
    cfg = {
        "default_backend": "Proxy",
        "backends": {
            "Proxy": {
                "api": "openai",
                "api_key_file": "~/.config/proxy.token",
                "default_model": "gpt-4o",
            },
            "Gemini": {"api": "genai", "api_key_file": "~/.config/gemini.token"},
        },
    }
    name, b_cfg = get_default_backend_config(cfg)
    assert name == "Proxy"
    assert b_cfg["api"] == "openai"

    name, b_cfg = get_default_backend_config(None)
    assert name is None
    assert b_cfg is None


def test_set_default_backend(tmp_path):
    config_file = tmp_path / "config.json"
    data = {
        "default_backend": "Gemini",
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

    updated = set_default_backend("Custom 1", config_path=str(config_file))
    assert updated["default_backend"] == "Custom 1"
    assert "active_backend" not in updated

    # Verify persisted
    reloaded = load_global_config(config_path=str(config_file))
    assert reloaded["default_backend"] == "Custom 1"
    assert "active_backend" not in reloaded


def test_set_default_backend_unknown(tmp_path):
    config_file = tmp_path / "config.json"
    data = create_default_global_config()
    save_global_config(data, config_path=str(config_file))

    with pytest.raises(ValueError, match="Backend 'NonExistent' not found"):
        set_default_backend("NonExistent", config_path=str(config_file))


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
    # Anthropic backends require API key by default
    assert backend_requires_api_key({"api": "anthropic"}) is True
    assert (
        backend_requires_api_key({"api": "anthropic", "api_key_file": "~/.token"})
        is True
    )
    assert (
        backend_requires_api_key({"api": "anthropic", "requires_api_key": False})
        is False
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
