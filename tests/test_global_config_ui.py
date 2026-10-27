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
        "active_backend": "Gemini",
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
    assert "active_backend = Gemini" in text
    assert "* Gemini" in text
    assert "  Proxy" in text
    assert "base_url = https://proxy.example/v1" in text
    assert ("global", "active_backend") in line_map.values()
    assert ("backend", "Proxy") in line_map.values()
    assert ("backend_field", "Proxy", "default_model") in line_map.values()


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
        patch("vimini.config.load_global_config", return_value=_sample_global_config()),
    ):
        vim.command.reset_mock()
        config.global_config_command()

        assert mock_buf.options.get("buftype") == "nofile"
        assert mock_buf.options.get("swapfile") is False
        assert mock_buf.options.get("modifiable") is False
        assert mock_buf.options.get("readonly") is True
        assert mock_buf.vars.get("vimini_global_config_path") == config_path
        assert any("Vimini Global Configuration" in line for line in mock_buf)


def test_apply_backend_config_updates_runtime():
    import vim
    from vimini import main, util

    cfg = _sample_global_config()
    with (
        patch("vimini.common.config.set_active_backend") as mock_set_active,
        patch("vimini.main.send_setup") as mock_send_setup,
    ):
        main.apply_backend_config(cfg, "Proxy", persist=True)

        mock_set_active.assert_called_once_with("Proxy")
        assert util._BACKEND == "Proxy"
        assert util._MODEL == "gpt-4o"
        assert util._API_KEY_FILE == os.path.expanduser("~/.config/proxy.token")
        mock_send_setup.assert_called_once()
        assert any(
            "g:vimini_backend" in call.args[0] for call in vim.command.call_args_list
        )
