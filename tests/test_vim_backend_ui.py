import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini import main, util
from vimini.models import build_models_buffer


def test_backend_command_without_arguments():
    util._BACKEND = "Gemini"
    config = {
        "default_backend": "Gemini",
        "backends": {
            "Gemini": {"api": "genai", "api_key_file": "~/.config/gemini.token"},
            "Custom 1": {
                "api": "openai",
                "api_key_file": "~/.config/c1.token",
                "default_model": "gpt-4o",
            },
            "Custom 2": {
                "api": "openai",
                "api_key_file": "~/.config/c2.token",
                "default_model": "gpt-4o-mini",
            },
        },
    }

    with (
        patch("vimini.common.config._GLOBAL_CONFIG", config),
        patch("vimini.common.config.load_global_config") as mock_load,
        patch("vimini.util.display_message") as mock_display,
    ):
        main.backend_command(None)
        mock_display.assert_called_once()
        mock_load.assert_not_called()
        msg = mock_display.call_args[0][0]
        assert "Gemini (available: Custom 1, Custom 2)" in msg


def test_backend_command_without_arguments_no_others():
    util._BACKEND = "Gemini"
    config = {
        "default_backend": "Gemini",
        "backends": {
            "Gemini": {"api": "genai", "api_key_file": "~/.config/gemini.token"},
        },
    }

    with (
        patch("vimini.common.config._GLOBAL_CONFIG", config),
        patch("vimini.common.config.load_global_config") as mock_load,
        patch("vimini.util.display_message") as mock_display,
    ):
        main.backend_command("")
        mock_display.assert_called_once()
        msg = mock_display.call_args[0][0]
        assert "Gemini (no other backends configured)" in msg


def test_backend_command_without_arguments_whitespace():
    util._BACKEND = "Gemini"
    config = {
        "default_backend": "Gemini",
        "backends": {
            "Gemini": {"api": "genai", "api_key_file": "~/.config/gemini.token"},
        },
    }

    with (
        patch("vimini.common.config._GLOBAL_CONFIG", config),
        patch("vimini.common.config.load_global_config") as mock_load,
        patch("vimini.util.display_message") as mock_display,
    ):
        main.backend_command("   ")
        mock_display.assert_called_once()
        msg = mock_display.call_args[0][0]
        assert "Gemini (no other backends configured)" in msg


def test_backend_command_switch_success_does_not_persist_to_config():
    util._BACKEND = "Gemini"
    config = {
        "default_backend": "Gemini",
        "backends": {
            "Gemini": {
                "api": "genai",
                "api_key_file": "~/.config/gemini.token",
                "default_model": "gemini-3.8-flash",
            },
            "Custom 1": {
                "api": "openai",
                "default_model": "gpt-4o",
                "api_key_file": "~/.config/proxy.token",
            },
        },
    }

    with (
        patch("vimini.common.config._GLOBAL_CONFIG", config),
        patch("vimini.common.config.load_global_config") as mock_load,
        patch("vimini.common.config.save_global_config") as mock_save,
        patch("vimini.main.send_setup") as mock_send_setup,
        patch("vimini.util.display_message") as mock_display,
    ):
        main.backend_command("Custom 1")

        mock_load.assert_not_called()
        mock_save.assert_not_called()
        assert config["default_backend"] == "Gemini"
        assert "active_backend" not in config
        assert util._BACKEND == "Custom 1"
        assert util._MODEL == "gpt-4o"
        assert util._API_KEY_FILE == os.path.expanduser("~/.config/proxy.token")
        assert util._GENAI_CLIENT is None
        mock_send_setup.assert_called_once()
        mock_display.assert_called_once()
        assert (
            "Switched backend to Custom 1 (model: gpt-4o)"
            in mock_display.call_args[0][0]
        )

        # Calling :ViminiBackend without arguments now reports the runtime active backend
        mock_display.reset_mock()
        main.backend_command(None)
        mock_display.assert_called_once()
        assert "Custom 1 (available: Gemini)" in mock_display.call_args[0][0]


def test_backend_command_switch_resets_key_and_model_when_missing():
    config = {
        "default_backend": "Gemini",
        "backends": {
            "Gemini": {
                "api": "genai",
                "api_key_file": "~/.config/gemini.token",
                "default_model": "gemini-3.8-flash",
            },
            "Local": {
                "api": "openai",
                "base_url": "http://localhost:11434/v1",
                "default_model": "llama3",
            },
        },
    }

    util._BACKEND = "Gemini"
    util._MODEL = "gemini-3.8-flash"
    util._API_KEY_FILE = os.path.expanduser("~/.config/gemini.token")
    util._MODEL_NAME = "Cached Model Name"
    util._GENAI_CLIENT = object()

    with (
        patch("vimini.common.config._GLOBAL_CONFIG", config),
        patch("vimini.common.config.load_global_config") as mock_load,
        patch("vimini.common.config.save_global_config") as mock_save,
        patch("vimini.main.send_setup") as mock_send_setup,
        patch("vimini.util.display_message") as mock_display,
    ):
        main.backend_command("Local")

        mock_load.assert_not_called()
        mock_save.assert_not_called()
        assert util._BACKEND == "Local"
        assert util._MODEL == "llama3"
        assert util._MODEL_NAME is None
        assert util._API_KEY_FILE is None
        assert util._GENAI_CLIENT is None
        mock_send_setup.assert_called_once()
        mock_display.assert_called_once()


def test_backend_command_switch_invalid():
    config = {
        "default_backend": "Gemini",
        "backends": {
            "Gemini": {"api": "genai", "api_key_file": "~/.config/gemini.token"},
        },
    }

    with (
        patch("vimini.common.config._GLOBAL_CONFIG", config),
        patch("vimini.util.display_message") as mock_display,
    ):
        main.backend_command("NonExistent")
        mock_display.assert_called_once()
        assert mock_display.call_args.kwargs.get("error") is True
        assert "not found" in mock_display.call_args[0][0]


def test_get_available_backends():
    config = {
        "default_backend": "Gemini",
        "backends": {
            "Gemini": {"api": "genai", "api_key_file": "~/.config/gemini.token"},
            "Custom 1": {
                "api": "openai",
                "api_key_file": "~/.config/c1.token",
                "default_model": "gpt-4o",
            },
            "Custom 2": {
                "api": "openai",
                "api_key_file": "~/.config/c2.token",
                "default_model": "gpt-4o-mini",
            },
        },
    }

    with (
        patch("vimini.common.config._GLOBAL_CONFIG", config),
        patch("vimini.common.config.load_global_config") as mock_load,
    ):
        all_b = main.get_available_backends()
        assert all_b == ["Gemini", "Custom 1", "Custom 2"]

        cust = main.get_available_backends("cust")
        assert cust == ["Custom 1", "Custom 2"]
        mock_load.assert_not_called()


def test_build_models_buffer_openai_single_group():
    models = [
        {"name": "gpt-4o", "display_name": "GPT-4o"},
        {"name": "o3-mini", "display_name": "o3-mini"},
    ]

    buffer_lines, line_map, current_line = build_models_buffer(
        models, current_model="gpt-4o", backend_name="Custom 1", api_type="openai"
    )

    header = buffer_lines[0]
    assert "Backend: Custom 1" in header
    assert "[Available Models]" in buffer_lines
    assert " * GPT-4o" in buffer_lines
    assert "   o3-mini" in buffer_lines
    # No restriction note in openai mode
    assert not any(
        "Only 'Multimodal & Text Generation'" in line for line in buffer_lines
    )


def test_build_models_buffer_genai_categories():
    models = [
        {
            "name": "gemini-2.5-flash",
            "display_name": "Gemini 2.5 Flash",
            "supported_actions": ["generateContent"],
        }
    ]

    buffer_lines, line_map, current_line = build_models_buffer(
        models,
        current_model="gemini-2.5-flash",
        backend_name="Gemini",
        api_type="genai",
    )

    header = buffer_lines[0]
    assert "Backend: Gemini" in header
    assert "[Multimodal & Text Generation]" in buffer_lines
    assert any("Only 'Multimodal & Text Generation'" in line for line in buffer_lines)


def test_initialize_uses_global_config_default_backend_and_ignores_active_backend():
    config = {
        "default_backend": "Gemini",
        "active_backend": "OtherBackend",
        "backends": {
            "Gemini": {
                "api": "genai",
                "api_key_file": "~/.config/gemini.token",
                "default_model": "gemini-global-default",
            },
            "OtherBackend": {
                "api": "openai",
                "api_key_file": "~/.config/other.token",
                "default_model": "other-model",
            },
        },
    }

    with (
        patch("vimini.common.config.load_global_config", return_value=config),
        patch("vimini.context.restore_context_files"),
        patch("vimini.util.get_api_key", return_value="token"),
    ):
        main.initialize(
            api_key_file="~/.config/gemini.token",
            model="deprecated-vim-config-model",
            logfile=None,
        )

    assert util._BACKEND == "Gemini"
    assert util._MODEL == "gemini-global-default"
    assert util._API_KEY_FILE == os.path.expanduser("~/.config/gemini.token")
    # active_backend is not removed from config
    assert config["active_backend"] == "OtherBackend"


def test_send_setup_does_not_send_api_key_file():
    util._BACKEND = "Gemini"
    util._MODEL = "gemini-3.8-flash"
    util._API_KEY_FILE = os.path.expanduser("~/.config/gemini.token")

    with (
        patch("vimini.handler.register_handler"),
        patch("vimini.util.send_channel_request") as mock_send,
    ):
        main.send_setup()

    req = mock_send.call_args[0][0]
    params = req["params"]
    assert params == {"backend": "Gemini", "model": "gemini-3.8-flash"}
    assert "api_key_file" not in params
    assert "api_key" not in params


def test_initialize_reports_invalid_global_config():
    with (
        patch(
            "vimini.common.config.load_global_config",
            side_effect=ValueError(
                "Backend 'Gemini' must specify a non-empty 'api_key_file'"
            ),
        ),
        patch("vimini.context.restore_context_files"),
        patch("vimini.util.get_api_key", return_value="token"),
        patch("vimini.util.display_message") as mock_display,
    ):
        main.initialize(api_key_file="~/.config/gemini.token", logfile=None)

    mock_display.assert_called_once_with(
        "Global configuration is invalid: Backend 'Gemini' must specify a non-empty 'api_key_file'",
        error=True,
    )


def test_initialize_does_not_warn_for_keyless_openai_backend():
    config = {
        "default_backend": "Local",
        "backends": {
            "Local": {
                "api": "openai",
                "base_url": "http://localhost:11434/v1",
                "default_model": "local-model",
            }
        },
    }

    util._API_KEY_FILE = os.path.expanduser("~/.config/gemini.token")
    util._GENAI_CLIENT = object()

    with (
        patch("vimini.common.config.load_global_config", return_value=config),
        patch("vimini.context.restore_context_files"),
        patch("vimini.util.get_api_key", return_value=None),
        patch("vimini.util.display_message") as mock_display,
    ):
        main.initialize(api_key_file="~/.config/gemini.token", logfile=None)

    assert util._BACKEND == "Local"
    assert util._MODEL == "local-model"
    assert util._API_KEY_FILE is None
    assert util._GENAI_CLIENT is None
    mock_display.assert_not_called()


def test_initialize_warns_with_backend_specific_api_key_message():
    config = {
        "default_backend": "Remote",
        "backends": {
            "Remote": {
                "api": "openai",
                "requires_api_key": True,
                "api_key_file": "~/.config/remote.token",
                "default_model": "gpt-compatible",
            }
        },
    }

    with (
        patch("vimini.common.config.load_global_config", return_value=config),
        patch("vimini.context.restore_context_files"),
        patch("vimini.util.get_api_key", return_value=None),
        patch("vimini.util.display_message") as mock_display,
    ):
        main.initialize(api_key_file="~/.config/gemini.token", logfile=None)

    mock_display.assert_called_once_with(
        "API key for backend 'Remote' not found in ~/.config/remote.token.",
        error=True,
    )


def test_initialize_warns_when_api_key_file_specified_without_requires_api_key():
    config = {
        "default_backend": "Remote",
        "backends": {
            "Remote": {
                "api": "openai",
                "api_key_file": "~/.config/remote.token",
                "default_model": "gpt-compatible",
            }
        },
    }

    with (
        patch("vimini.common.config.load_global_config", return_value=config),
        patch("vimini.context.restore_context_files"),
        patch("vimini.util.get_api_key", return_value=None),
        patch("vimini.util.display_message") as mock_display,
    ):
        main.initialize(api_key_file="~/.config/gemini.token", logfile=None)

    mock_display.assert_called_once_with(
        "API key for backend 'Remote' not found in ~/.config/remote.token.",
        error=True,
    )
