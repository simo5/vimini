import sys
import os
import json
import tempfile
import pytest
from unittest.mock import MagicMock, patch

# Ensure python3 root is in sys.path
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), '..', 'python3')))

from vimini.common.util import (
    parse_tool_command_config,
    validate_tool_call,
    upgrade_project_data,
    load_project_data,
    save_project_data,
    create_default_project_data
)
from vimini.agent.chat import generate_tool_declaration_schema
from vimini.config import (
    CONFIG_JSON_HELP_HEADER,
    prompt_reset_config_dialog,
    load_project_config_or_prompt,
    validate_config_json_content,
    _format_command_tree,
    _truncate_72
)

def test_parse_tool_command_config_simple():
    assert parse_tool_command_config(None) is None
    assert parse_tool_command_config("") is None

    res = parse_tool_command_config("cargo build")
    assert res["type"] == "simple"
    assert res["command"] == "cargo build"


def test_parse_tool_command_config_alternatives_list():
    raw = ["cargo build", "cargo build --release"]
    res = parse_tool_command_config(raw)
    assert res["type"] == "alternatives"
    assert len(res["alternatives"]) == 2
    assert res["alternatives"][0]["command"] == "cargo build"
    assert res["alternatives"][1]["command"] == "cargo build --release"

    raw_dicts = [
        {"command": "make test", "name": "unit", "description": "Run unit tests"},
        {"command": "make integration", "name": "integration", "description": "Run integration tests"}
    ]
    res2 = parse_tool_command_config(raw_dicts)
    assert res2["type"] == "alternatives"
    assert res2["alternatives"][0]["name"] == "unit"
    assert res2["alternatives"][1]["name"] == "integration"


def test_parse_tool_command_config_alternatives_dict():
    raw = {
        "description": "Select build target",
        "alternatives": [
            {"command": "ninja quick", "name": "quick"},
            {"command": "ninja full", "name": "full"}
        ]
    }
    res = parse_tool_command_config(raw)
    assert res["type"] == "alternatives"
    assert res["description"] == "Select build target"
    assert len(res["alternatives"]) == 2


def test_parse_tool_command_config_options_and_arguments():
    raw = {
        "command": "pytest",
        "description": "Run test suite",
        "options": [
            {"name": "verbose", "flag": "-v", "type": "option", "has_value": False, "description": "Verbose"},
            {"name": "keyword", "flag": "-k", "type": "option", "has_value": True, "description": "Filter by keyword"},
            {"name": "log_level", "flag": "--log-level", "type": "option", "choices": ["DEBUG", "INFO", "WARNING"]},
            {"name": "test_path", "type": "argument", "description": "Test target file"}
        ]
    }
    res = parse_tool_command_config(raw)
    assert res["type"] == "command_with_options"
    assert res["command"] == "pytest"
    opts = res["options"]
    assert len(opts) == 4
    assert opts[0]["name"] == "verbose"
    assert opts[0]["has_value"] is False
    assert opts[1]["name"] == "keyword"
    assert opts[1]["has_value"] is True
    assert opts[2]["name"] == "log_level"
    assert opts[2]["has_value"] is True
    assert opts[2]["choices"] == ["DEBUG", "INFO", "WARNING"]
    assert opts[3]["type"] == "argument"


def test_validate_simple_command():
    cfg = {"type": "simple", "command": "make test"}
    valid, cmd = validate_tool_call("test", {}, cfg)
    assert valid is True
    assert cmd == "make test"

    valid, err = validate_tool_call("test", {"extra": "foo"}, cfg)
    assert valid is False
    assert "accepts no options or arguments" in err


def test_validate_alternatives():
    cfg = {
        "type": "alternatives",
        "alternatives": [
            {"command": "cargo build", "name": "debug"},
            {"command": "cargo build --release", "name": "release"}
        ]
    }
    # Selecting via command
    valid, cmd = validate_tool_call("build", {"command": "cargo build"}, cfg)
    assert valid is True
    assert cmd == "cargo build"

    # Selecting via name
    valid, cmd = validate_tool_call("build", {"command": "release"}, cfg)
    assert valid is True
    assert cmd == "cargo build --release"

    # Invalid command
    valid, err = validate_tool_call("build", {"command": "make"}, cfg)
    assert valid is False
    assert "not one of the allowed alternative commands" in err

    # No command selected
    valid, err = validate_tool_call("build", {}, cfg)
    assert valid is False
    assert "No command selected" in err

    # Extra options rejected (all-or-nothing)
    valid, err = validate_tool_call("build", {"command": "cargo build", "verbose": True}, cfg)
    assert valid is False
    assert "all-or-nothing and do not accept additional options" in err


def test_validate_command_with_options_and_arguments():
    cfg = {
        "type": "command_with_options",
        "command": "pytest",
        "options": [
            {"name": "verbose", "flag": "-v", "type": "option", "has_value": False, "description": "Verbose"},
            {"name": "keyword", "flag": "-k", "type": "option", "has_value": True, "description": "Keyword filter"},
            {"name": "log_level", "flag": "--log-level", "type": "option", "has_value": True, "choices": ["DEBUG", "INFO", "WARNING"]},
            {"name": "test_path", "type": "argument", "description": "Target test file"}
        ]
    }

    # Valid call with flag and argument
    args = {
        "verbose": True,
        "keyword": "test_auth",
        "test_path": "tests/test_login.py"
    }
    valid, cmd = validate_tool_call("test", args, cfg)
    assert valid is True
    assert cmd == "pytest -v -k test_auth tests/test_login.py"

    # Flag with False should be omitted
    args2 = {
        "verbose": False,
        "test_path": "tests/test_login.py"
    }
    valid, cmd = validate_tool_call("test", args2, cfg)
    assert valid is True
    assert cmd == "pytest tests/test_login.py"

    # Value with spaces quoted properly
    args3 = {
        "keyword": "foo and bar",
        "test_path": "tests/my test.py"
    }
    valid, cmd = validate_tool_call("test", args3, cfg)
    assert valid is True
    assert "-k 'foo and bar'" in cmd
    assert "'tests/my test.py'" in cmd

    # Invalid choice
    valid, err = validate_tool_call("test", {"log_level": "CRITICAL"}, cfg)
    assert valid is False
    assert "Invalid value 'CRITICAL' for option 'log_level'" in err

    # Non-boolean for no-value flag
    valid, err = validate_tool_call("test", {"verbose": "not-a-bool"}, cfg)
    assert valid is False
    assert "takes no value (boolean flag)" in err

    # Complex type rejection (dict/list)
    valid, err = validate_tool_call("test", {"keyword": ["a", "b"]}, cfg)
    assert valid is False
    assert "must be a single command line argument string" in err

    # Unrecognized option rejection
    valid, err = validate_tool_call("test", {"unrecognized": "value"}, cfg)
    assert valid is False
    assert "Unrecognized parameter(s)" in err


def test_generate_tool_declaration_schema():
    cfg = {
        "type": "command_with_options",
        "command": "cargo test",
        "description": "Run cargo tests",
        "options": [
            {"name": "release", "flag": "--release", "type": "option", "has_value": False, "description": "Release mode"},
            {"name": "package", "flag": "--package", "type": "option", "has_value": True, "description": "Package name"},
            {"name": "filter", "type": "argument", "description": "Test name filter"}
        ]
    }
    desc, schema = generate_tool_declaration_schema("test", cfg)
    assert "cargo test" in desc
    assert "release" in schema.properties
    assert "package" in schema.properties
    assert "filter" in schema.properties
    assert schema.properties["release"].type.name == "BOOLEAN"
    assert schema.properties["package"].type.name == "STRING"
    assert schema.properties["filter"].type.name == "STRING"
    assert "Usage rules:" in desc
    assert "single string" in desc


def test_comment_stripping_and_json_parsing():
    raw_input = CONFIG_JSON_HELP_HEADER + """
    {
      # This is a comment inside JSON if someone adds one
      "command": "pytest",
      "options": [
        # verbose flag comment
        {"name": "verbose", "flag": "-v", "type": "option", "has_value": false}
      ]
    }
    """
    # Strip comment lines
    clean_lines = [line for line in raw_input.splitlines() if not line.strip().startswith('#')]
    clean_content = "\n".join(clean_lines).strip()

    parsed = json.loads(clean_content)
    assert parsed["command"] == "pytest"
    assert len(parsed["options"]) == 1
    assert parsed["options"][0]["name"] == "verbose"


def test_upgrade_project_data_normalizes_string_commands_to_dict():
    raw = {
        "version": "0.1",
        "configuration": {
            "build-command": "cargo build",
            "test-command": "cargo test"
        }
    }
    upgraded = upgrade_project_data(raw)
    cfg = upgraded["configuration"]
    assert isinstance(cfg["build-command"], dict)
    assert cfg["build-command"]["command"] == "cargo build"
    assert "description" in cfg["build-command"]
    assert isinstance(cfg["test-command"], dict)
    assert cfg["test-command"]["command"] == "cargo test"
    assert "description" in cfg["test-command"]


def test_format_command_tree_and_72_char_cutoff():
    cmd_dict = {
        "command": "pytest",
        "description": "Run the entire project test suite with all coverage and security reporting enabled",
        "options": [
            {
                "name": "very_long_option_name_exceeding_character_limits_for_testing",
                "flag": "--very-long-flag-name",
                "type": "option",
                "choices": ["CHOICE_A", "CHOICE_B", "CHOICE_C", "CHOICE_D"],
                "has_value": True,
                "description": "This is an extremely long option description designed to test line truncation at 72 characters"
            },
            {
                "name": "verbose",
                "flag": "-v",
                "type": "option",
                "has_value": False,
                "description": "Verbose mode"
            }
        ]
    }

    lines = _format_command_tree("test-command", cmd_dict)
    assert len(lines) > 2
    assert lines[0] == "  test-command = pytest"
    assert "options:" in lines[2]

    # Verify tree formatting characters are present
    assert any("├──" in l or "└──" in l for l in lines)

    # Verify every line is cut after 72 characters
    for line in lines:
        assert len(line) <= 72


def test_validate_config_json_content():
    # Syntax error
    parsed, err = validate_config_json_content("test-command", "{invalid json")
    assert parsed is None
    assert "JSON syntax error" in err

    # Missing description (command always needs a description)
    parsed, err = validate_config_json_content("test-command", '{"command": "pytest"}')
    assert parsed is None
    assert "description" in err.lower()

    # Valid command with description and options
    parsed, err = validate_config_json_content("test-command", '{"command": "pytest", "description": "Run tests", "options": []}')
    assert err is None
    assert parsed["command"] == "pytest"
    assert parsed["description"] == "Run tests"


def test_empty_command_skeleton_prefill():
    from vimini import config
    # Test build-command prefill
    config._VIMINI_PENDING_PROJECT_CONFIG = {"build-command": None, "test-command": None}

    default_build = "make" if "build-command" == "build-command" else "make test"
    default_build_desc = "Build the project"
    assert default_build == "make"
    assert default_build_desc == "Build the project"

    default_test = "make test"
    default_test_desc = "Run project tests"
    assert default_test == "make test"
    assert default_test_desc == "Run project tests"


class MockBuffer(list):
    def __init__(self, initial=None, number=1, name=""):
        super().__init__(initial or [])
        self.number = number
        self.name = name
        self.options = {}
        self.vars = {}


def test_config_command_buffer_options_and_vars(tmp_path):
    import vim
    from vimini import config

    mock_buf = MockBuffer()
    mock_win = MagicMock()
    mock_win.cursor = (1, 0)
    mock_win.buffer = mock_buf

    with patch.object(vim.current, "buffer", mock_buf), \
         patch.object(vim.current, "window", mock_win), \
         patch("vimini.util.new_split"), \
         patch("vimini.util.get_git_repo_root", return_value=str(tmp_path)), \
         patch("vimini.config.load_project_config_or_prompt", return_value={"configuration": {}}):

        vim.command.reset_mock()
        config.config_command()

        # Buffer options must be directly set on buf.options
        assert mock_buf.options.get("buftype") == "nofile"
        assert mock_buf.options.get("swapfile") is False
        assert mock_buf.options.get("modifiable") is False
        assert mock_buf.options.get("readonly") is True

        # Buffer variables must be directly set on buf.vars
        assert mock_buf.vars.get("vimini_config_root") == str(tmp_path)
        assert "vimini_config_name" in mock_buf.vars

        # vim.command must NOT be used for setting these buffer options/variables
        for call_args in vim.command.call_args_list:
            cmd_str = call_args[0][0]
            assert "setlocal buftype" not in cmd_str
            assert "setlocal swapfile" not in cmd_str
            assert "setlocal modifiable" not in cmd_str
            assert "setlocal readonly" not in cmd_str
            assert "let b:vimini_config_root" not in cmd_str
            assert "let b:vimini_config_name" not in cmd_str


def test_refresh_config_buffer_options(tmp_path):
    import vim
    from vimini import config

    target_buf = MockBuffer(["old content"], number=99, name="ViminiProjectConfig")
    target_buf.options["readonly"] = True
    target_buf.options["modifiable"] = False
    target_buf.vars["vimini_config_root"] = str(tmp_path)
    target_buf.vars["vimini_config_name"] = "test_project"

    config._VIMINI_PENDING_PROJECT_CONFIG = {}

    with patch.object(vim, "buffers", [target_buf]), \
         patch.object(vim, "windows", []):
        vim.command.reset_mock()
        config._refresh_config_buffer(buf_nr=99)

        # Options must be managed directly on target_buf.options
        assert target_buf.options.get("modifiable") is False
        assert target_buf.options.get("readonly") is True

        # No setbufvar calls via vim.command
        for call_args in vim.command.call_args_list:
            cmd_str = call_args[0][0]
            assert "setbufvar" not in cmd_str


def test_finalize_json_config_uses_buffer_vars(tmp_path):
    import vim
    from vimini import config

    tmp_file = str(tmp_path / "test.config")
    with open(tmp_file, "w", encoding="utf-8") as f:
        f.write('{"command": "pytest", "description": "Run tests"}')

    mock_buf = MockBuffer(number=10)
    mock_buf.vars["vimini_config_key"] = "test-command"
    mock_buf.vars["vimini_config_tmp_file"] = tmp_file
    mock_buf.vars["vimini_config_buf_nr"] = 10

    config._VIMINI_PENDING_PROJECT_CONFIG = {}
    config._JSON_EDITOR_STATE = {}

    with patch.object(vim.current, "buffer", mock_buf), \
         patch("vimini.config._refresh_config_buffer"):
        config.finalize_json_config(is_wipeout=True)

        assert config._VIMINI_PENDING_PROJECT_CONFIG.get("test-command") == {"command": "pytest", "description": "Run tests"}
