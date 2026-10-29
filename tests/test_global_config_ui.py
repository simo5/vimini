import copy
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)


class MockBuffer(list):
    def __init__(self, initial=None, number=1, name=""):
        super().__init__(initial or [])
        self.number = number
        self.name = name
        self.options = {}
        self.vars = {}


def _sample_global_config():
    return {
        "default_backend": "Gemini",
        "backends": {
            "Gemini": {
                "api": "genai",
                "api_key_file": "~/.config/gemini.token",
                "default_model": "gemini-3.8-flash",
            },
            "Proxy": {
                "api": "openai",
                "base_url": "https://proxy.example/v1",
                "api_key_file": "~/.config/proxy.token",
                "default_model": "gpt-4o",
            },
        },
    }


def test_draw_global_config_listing_contains_backends():
    from vimini.config import _draw_global_config_listing

    lines, line_map = _draw_global_config_listing(
        _sample_global_config(), "/tmp/vimini/config.json"
    )
    text = "\n".join(lines)

    assert "Vimini Global Configuration" in text
    assert "default_backend = Gemini" in text
    assert "active_backend" not in text
    assert "> UI PREFERENCES" in text
    assert "thinking_color = DarkGray (default)" in text
    assert "* Gemini" in text
    assert "  Proxy" in text
    assert "base_url = https://proxy.example/v1" in text
    assert ("ui", "thinking_color") in line_map.values()
    assert ("global", "default_backend") in line_map.values()
    assert ("backend", "Proxy") in line_map.values()
    assert ("backend_field", "Proxy", "default_model") in line_map.values()


def test_anthropic_backend_fields_listed_and_saved_as_ints():
    import vim
    from vimini import config

    cfg = {
        "default_backend": "Claude",
        "backends": {
            "Claude": {
                "api": "anthropic",
                "api_key_file": "~/.config/anthropic.token",
                "default_model": "claude-3-7-sonnet-20250219",
            }
        },
    }
    lines, line_map = config._draw_global_config_listing(cfg, "/tmp/vimini/config.json")
    text = "\n".join(lines)
    assert "max_tokens = (not set)" in text
    assert "thinking_budget_tokens = (not set)" in text
    assert ("backend_field", "Claude", "max_tokens") in line_map.values()
    assert ("backend_field", "Claude", "thinking_budget_tokens") in line_map.values()

    config._VIMINI_PENDING_GLOBAL_CONFIG = copy.deepcopy(cfg)
    mock_win = MagicMock()
    mock_win.cursor = (1, 0)
    mock_win.buffer = MockBuffer()

    with (
        patch.object(vim.current, "window", mock_win),
        patch(
            "vimini.config._get_global_target_at_cursor",
            return_value=("backend_field", "Claude", "max_tokens"),
        ),
        patch.object(vim, "eval", return_value="8192"),
        patch("vimini.config._refresh_global_config_buffer"),
        patch("vimini.util.display_message"),
    ):
        config.edit_global_config_option()
        assert (
            config._VIMINI_PENDING_GLOBAL_CONFIG["backends"]["Claude"]["max_tokens"]
            == 8192
        )
        assert isinstance(
            config._VIMINI_PENDING_GLOBAL_CONFIG["backends"]["Claude"]["max_tokens"],
            int,
        )


def test_draw_and_delete_legacy_active_backend_in_global_config():
    import vim
    from vimini import config

    cfg = _sample_global_config()
    cfg["active_backend"] = "Proxy"

    lines, line_map = config._draw_global_config_listing(cfg, "/tmp/vimini/config.json")
    text = "\n".join(lines)

    assert "default_backend = Gemini" in text
    assert "active_backend = Proxy" in text
    assert ("global", "active_backend") in line_map.values()
    # Default backend is Gemini, so Gemini has the '*' marker (active_backend is ignored)
    assert "* Gemini" in text
    assert "  Proxy" in text

    config._VIMINI_PENDING_GLOBAL_CONFIG = cfg
    mock_win = MagicMock()
    mock_win.cursor = (1, 0)
    mock_win.buffer = MockBuffer()

    with (
        patch.object(vim.current, "window", mock_win),
        patch(
            "vimini.config._get_global_target_at_cursor",
            return_value=("global", "active_backend"),
        ),
        patch("vimini.config._refresh_global_config_buffer"),
        patch("vimini.util.display_message") as mock_msg,
    ):
        config.delete_or_clear_global_config_option()

        assert "active_backend" not in config._VIMINI_PENDING_GLOBAL_CONFIG
        assert config._VIMINI_PENDING_GLOBAL_CONFIG["default_backend"] == "Gemini"
        mock_msg.assert_called_once_with("Deleted 'active_backend' from configuration.")


def test_global_config_command_buffer_options_and_vars(tmp_path):
    import vim
    from vimini import config

    mock_buf = MockBuffer()
    mock_win = MagicMock()
    mock_win.cursor = (1, 0)
    mock_win.buffer = mock_buf
    mock_win.options = {}

    config_path = str(tmp_path / "config.json")
    with (
        patch.object(vim.current, "buffer", mock_buf),
        patch.object(vim.current, "window", mock_win),
        patch("vimini.util.new_split"),
        patch("vimini.config.get_global_config_path", return_value=config_path),
        patch("vimini.config.get_global_config", return_value=_sample_global_config()),
    ):
        vim.command.reset_mock()
        config.global_config_command()

        assert mock_buf.options.get("buftype") == "nofile"
        assert mock_buf.options.get("swapfile") is False
        assert mock_buf.options.get("modifiable") is False
        assert mock_buf.options.get("readonly") is True
        assert mock_buf.vars.get("vimini_global_config_path") == config_path
        assert any("Vimini Global Configuration" in line for line in mock_buf)


def test_apply_backend_config_updates_runtime_only():
    import vim
    from vimini import main, util

    cfg = _sample_global_config()
    with (
        patch("vimini.common.config.save_global_config") as mock_save,
        patch("vimini.main.send_setup") as mock_send_setup,
    ):
        main.apply_backend_config(cfg, "Proxy")

        mock_save.assert_not_called()
        assert "active_backend" not in cfg
        assert cfg["default_backend"] == "Gemini"
        assert util._BACKEND == "Proxy"
        assert util._MODEL == "gpt-4o"
        assert util._API_KEY_FILE == os.path.expanduser("~/.config/proxy.token")
        mock_send_setup.assert_called_once()
        assert any(
            "g:vimini_backend" in call.args[0] for call in vim.command.call_args_list
        )


def test_prompt_delete_backend_dialog_confirm():
    import vim
    from vimini.config import prompt_delete_backend_dialog

    def mock_vim_eval(expr):
        if "popup_create" in expr:
            assert "Delete backend 'CustomBackend'?" in expr
            assert "Delete Backend" in expr
            return 101
        if "getchar" in expr:
            return ord("y")
        if "popup_close" in expr:
            return 0
        return ""

    with patch.object(vim, "eval", side_effect=mock_vim_eval):
        assert prompt_delete_backend_dialog("CustomBackend") is True


def test_prompt_delete_backend_dialog_reject():
    import vim
    from vimini.config import prompt_delete_backend_dialog

    def mock_vim_eval(expr):
        if "popup_create" in expr:
            return 101
        if "getchar" in expr:
            return ord("n")
        if "popup_close" in expr:
            return 0
        return ""

    with patch.object(vim, "eval", side_effect=mock_vim_eval):
        assert prompt_delete_backend_dialog("CustomBackend") is False


def test_prompt_delete_backend_dialog_exception():
    import vim
    from vimini.config import prompt_delete_backend_dialog

    with patch.object(vim, "eval", side_effect=Exception("popup failed")):
        assert prompt_delete_backend_dialog("CustomBackend") is False


def test_delete_backend_option_confirmed():
    import vim
    from vimini import config

    config._VIMINI_PENDING_GLOBAL_CONFIG = _sample_global_config()
    mock_win = MagicMock()
    mock_win.cursor = (1, 0)
    mock_win.buffer = MockBuffer()

    with (
        patch.object(vim.current, "window", mock_win),
        patch(
            "vimini.config._get_global_target_at_cursor",
            return_value=("backend", "Proxy"),
        ),
        patch(
            "vimini.config.prompt_delete_backend_dialog", return_value=True
        ) as mock_dialog,
        patch("vimini.config._refresh_global_config_buffer"),
        patch("vimini.util.display_message") as mock_msg,
    ):
        config.delete_or_clear_global_config_option()

        mock_dialog.assert_called_once_with("Proxy")
        assert "Proxy" not in config._VIMINI_PENDING_GLOBAL_CONFIG["backends"]
        mock_msg.assert_called_once_with("Deleted backend 'Proxy'.")


def test_delete_backend_option_rejected():
    import vim
    from vimini import config

    config._VIMINI_PENDING_GLOBAL_CONFIG = _sample_global_config()
    mock_win = MagicMock()
    mock_win.cursor = (1, 0)
    mock_win.buffer = MockBuffer()

    with (
        patch.object(vim.current, "window", mock_win),
        patch(
            "vimini.config._get_global_target_at_cursor",
            return_value=("backend", "Proxy"),
        ),
        patch(
            "vimini.config.prompt_delete_backend_dialog", return_value=False
        ) as mock_dialog,
        patch("vimini.config._refresh_global_config_buffer"),
        patch("vimini.util.display_message") as mock_msg,
    ):
        config.delete_or_clear_global_config_option()

        mock_dialog.assert_called_once_with("Proxy")
        assert "Proxy" in config._VIMINI_PENDING_GLOBAL_CONFIG["backends"]
        assert not any("Deleted backend" in str(c) for c in mock_msg.call_args_list)


def test_delete_backend_option_cannot_delete_last():
    import vim
    from vimini import config

    single_backend_cfg = {
        "default_backend": "Gemini",
        "backends": {
            "Gemini": {
                "api": "genai",
                "api_key_file": "~/.config/gemini.token",
            }
        },
    }
    config._VIMINI_PENDING_GLOBAL_CONFIG = single_backend_cfg
    mock_win = MagicMock()
    mock_win.cursor = (1, 0)
    mock_win.buffer = MockBuffer()

    with (
        patch.object(vim.current, "window", mock_win),
        patch(
            "vimini.config._get_global_target_at_cursor",
            return_value=("backend", "Gemini"),
        ),
        patch("vimini.config.prompt_delete_backend_dialog") as mock_dialog,
        patch("vimini.util.display_message") as mock_msg,
    ):
        config.delete_or_clear_global_config_option()

        mock_dialog.assert_not_called()
        assert "Gemini" in config._VIMINI_PENDING_GLOBAL_CONFIG["backends"]
        mock_msg.assert_called_once_with(
            "Cannot delete the last configured backend.", error=True
        )


def test_confirm_global_config_dialog_confirmed():
    import vim
    from vimini import config

    orig = _sample_global_config()
    pending = copy.deepcopy(orig)
    pending["default_backend"] = "Proxy"

    config._VIMINI_ORIGINAL_GLOBAL_CONFIG = orig
    config._VIMINI_PENDING_GLOBAL_CONFIG = pending
    config._VIMINI_GLOBAL_CONFIG_PATH = "/tmp/test_config.json"

    def mock_vim_eval(expr):
        if "popup_create" in expr:
            assert "Save global configuration changes?" in expr
            return 201
        if "getchar" in expr:
            return ord("y")
        if "popup_close" in expr:
            return 0
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_vim_eval),
        patch("vimini.config.save_global_config", return_value=True) as mock_save,
    ):
        config.confirm_global_config()
        mock_save.assert_called_once_with(pending, config_path="/tmp/test_config.json")


def test_edit_ui_preference_option():
    import vim
    from vimini import config

    config._VIMINI_PENDING_GLOBAL_CONFIG = _sample_global_config()
    mock_win = MagicMock()
    mock_win.cursor = (1, 0)
    mock_win.buffer = MockBuffer()

    with (
        patch.object(vim.current, "window", mock_win),
        patch(
            "vimini.config._get_global_target_at_cursor",
            return_value=("ui", "thinking_color"),
        ),
        patch.object(vim, "eval", return_value="Cyan"),
        patch("vimini.config._refresh_global_config_buffer"),
        patch("vimini.util.display_message") as mock_msg,
    ):
        config.edit_global_config_option()

        assert config._VIMINI_PENDING_GLOBAL_CONFIG["ui"]["thinking_color"] == "Cyan"
        mock_msg.assert_called_once_with("Set 'ui.thinking_color' to 'Cyan'.")


def test_delete_or_clear_ui_preference_option():
    import vim
    from vimini import config

    cfg = _sample_global_config()
    cfg["ui"] = {"thinking_color": "Cyan", "prompt_color": "Blue"}
    config._VIMINI_PENDING_GLOBAL_CONFIG = cfg
    mock_win = MagicMock()
    mock_win.cursor = (1, 0)
    mock_win.buffer = MockBuffer()

    with (
        patch.object(vim.current, "window", mock_win),
        patch(
            "vimini.config._get_global_target_at_cursor",
            return_value=("ui", "thinking_color"),
        ),
        patch("vimini.config._refresh_global_config_buffer"),
        patch("vimini.util.display_message") as mock_msg,
    ):
        config.delete_or_clear_global_config_option()

        assert "thinking_color" not in config._VIMINI_PENDING_GLOBAL_CONFIG["ui"]
        assert config._VIMINI_PENDING_GLOBAL_CONFIG["ui"]["prompt_color"] == "Blue"
        mock_msg.assert_called_once_with("Reset 'ui.thinking_color' to default.")
