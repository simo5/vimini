import vim
import os
import json
import tempfile
from . import util
from vimini.common.util import (
    PROJECT_CONFIG_SCHEMA,
    load_project_data,
    save_project_data,
    create_default_project_data,
    get_project_data_file_path,
    get_project_name
)

CONFIG_JSON_HELP_HEADER = """# Vimini Project Command Configuration
# Lines starting with '#' are comments and will be ignored when saving.
#
# You can configure commands in one of the following formats:
#
# 1. COMMAND (even with no options, a description is always required):
#    {
#      "command": "make test",
#      "description": "Run the project test suite"
#    }
#
# 2. ALTERNATIVE COMMANDS (all-or-nothing commands, agent selects one):
#    {
#      "alternatives": [
#        {"command": "cargo build", "description": "Debug build"},
#        {"command": "cargo build --release", "description": "Release build"}
#      ]
#    }
#
# 3. COMMAND WITH CONTROLLED OPTIONS AND ARGUMENTS:
#    {
#      "command": "pytest",
#      "description": "Run pytest suite",
#      "options": [
#        {
#          "name": "verbose",
#          "flag": "-v",
#          "type": "option",
#          "has_value": false,
#          "description": "Run tests with verbose output"
#        },
#        {
#          "name": "keyword",
#          "flag": "-k",
#          "type": "option",
#          "has_value": true,
#          "description": "Filter tests by keyword expression"
#        },
#        {
#          "name": "log_level",
#          "flag": "--log-level",
#          "type": "option",
#          "choices": ["DEBUG", "INFO", "WARNING", "ERROR"],
#          "description": "Set logging level"
#        },
#        {
#          "name": "test_path",
#          "type": "argument",
#          "description": "Target test file or test directory"
#        }
#      ]
#    }
#
"""

_VIMINI_PENDING_PROJECT_CONFIG = None
_VIMINI_ORIGINAL_PROJECT_CONFIG = None
_VIMINI_CONFIG_PROJECT_ROOT = None
_VIMINI_CONFIG_PROJECT_NAME = None
_JSON_EDITOR_STATE = {}

def _to_str(val):
    if isinstance(val, bytes):
        return val.decode('utf-8', errors='replace')
    return str(val) if val is not None else ""

def _get_key_for_line(line):
    if not line:
        return None
    trimmed = line.strip()
    if trimmed.startswith('|') or trimmed.startswith('>'):
        return None
    for key in PROJECT_CONFIG_SCHEMA.keys():
        if trimmed.startswith(f"{key} ") or trimmed.startswith(f"{key}=") or trimmed.startswith(f"{key}:") or trimmed == key:
            return key
    if _VIMINI_PENDING_PROJECT_CONFIG:
        for key in _VIMINI_PENDING_PROJECT_CONFIG.keys():
            if trimmed.startswith(f"{key} ") or trimmed.startswith(f"{key}=") or trimmed.startswith(f"{key}:") or trimmed == key:
                return key
    return None

def _find_key_at_or_above(buf, line_num):
    idx = line_num - 1
    while idx >= 0:
        line = buf[idx]
        trimmed = line.strip()
        if trimmed.startswith('>') or trimmed.startswith('|'):
            return None
        key = _get_key_for_line(line)
        if key:
            return key
        idx -= 1
    return None

def _truncate_72(line):
    return line[:72] if len(line) > 72 else line

def _format_command_tree(key, val, default_desc=""):
    lines = []
    if isinstance(val, str) and val.strip():
        if val.strip().startswith("{"):
            try:
                parsed_json = json.loads(val)
                if isinstance(parsed_json, dict):
                    val = parsed_json
                else:
                    val = {"command": val.strip(), "description": ""}
            except Exception:
                val = {"command": val.strip(), "description": ""}
        else:
            val = {"command": val.strip(), "description": ""}

    from vimini.common.util import parse_tool_command_config
    parsed = parse_tool_command_config(val)
    if not parsed:
        lines.append(f"  {key} = (not set)")
        if default_desc:
            lines.append(_truncate_72(f"    # {default_desc}"))
        return lines

    cmd_type = parsed.get("type")
    if cmd_type == "alternatives":
        lines.append(f"  {key} = (alternatives)")
        desc = parsed.get("description", "")
        alts = parsed.get("alternatives", [])
        has_alts = bool(alts)
        if desc and desc != "Select one of the configured alternative commands (all-or-nothing)":
            prefix = "├── " if has_alts else "└── "
            lines.append(_truncate_72(f"    {prefix}description: {desc}"))
        for i, alt in enumerate(alts):
            prefix = "└── " if i == len(alts) - 1 else "├── "
            c = alt.get("command", "")
            d = alt.get("description", "")
            alt_str = f"{c}: {d}" if d else c
            lines.append(_truncate_72(f"    {prefix}{alt_str}"))
        return lines

    base_cmd = parsed.get("command", "")
    lines.append(f"  {key} = {base_cmd}")
    desc = parsed.get("description", "")
    options = parsed.get("options", [])

    has_options = bool(options)
    if desc:
        prefix = "├── " if has_options else "└── "
        lines.append(_truncate_72(f"    {prefix}description: {desc}"))
    elif not has_options:
        lines.append("    └── description: (none)")

    if has_options:
        lines.append("    └── options:")
        for i, opt in enumerate(options):
            prefix = "└── " if i == len(options) - 1 else "├── "
            opt_type = opt.get("type", "option")
            opt_name = opt.get("name", "")
            opt_flag = opt.get("flag", "")
            opt_desc = opt.get("description", "")
            choices = opt.get("choices")
            has_val = opt.get("has_value", False)
            required = opt.get("required", False)

            req_str = " [required]" if required else ""
            if opt_type == "option":
                if has_val:
                    choices_str = f" [{', '.join(choices)}]" if choices else " <val>"
                    spec = f"{opt_name} ({opt_flag}{choices_str}){req_str}"
                else:
                    spec = f"{opt_name} ({opt_flag}){req_str}"
            else:
                choices_str = f" [{', '.join(choices)}]" if choices else ""
                spec = f"{opt_name} (arg{choices_str}){req_str}"

            line_str = f"        {prefix}{spec}: {opt_desc}" if opt_desc else f"        {prefix}{spec}"
            lines.append(_truncate_72(line_str))

    return lines

def _draw_config_listing(project_name, project_root, config_data, metadata_data):
    file_path = get_project_data_file_path(start_dir=project_root) or "(not saved)"
    context_files_count = len(metadata_data.get("files", []))
    version = metadata_data.get("version", "0.1")

    buffer_lines = [
        f"| Vimini Project Configuration: {project_name}",
        "|----------------------------------------------------------------------",
        "| <CR>/e: edit value | d: clear/unset | r: reset | q: close",
        f"| File: {file_path}",
        f"| Root: {project_root}",
        "",
        "> CONFIGURATION OPTIONS"
    ]

    all_keys = list(PROJECT_CONFIG_SCHEMA.keys())
    for k in config_data.keys():
        if k not in all_keys:
            all_keys.append(k)

    for key in all_keys:
        val = config_data.get(key)
        schema_info = PROJECT_CONFIG_SCHEMA.get(key, {})
        desc = schema_info.get("description", "")
        if key.endswith("-command") or (isinstance(val, dict) and "command" in val):
            buffer_lines.extend(_format_command_tree(key, val, desc))
        else:
            val_str = str(val) if val is not None else "(not set)"
            buffer_lines.append(_truncate_72(f"  {key} = {val_str}"))
            if desc:
                buffer_lines.append(_truncate_72(f"    # {desc}"))

    buffer_lines.extend([
        "",
        "> PROJECT METADATA (Read-Only)",
        f"  version = {version}",
        f"  context files = {context_files_count} file(s) tracked"
    ])

    return buffer_lines

def prompt_reset_config_dialog(reason=None):
    """
    Asks the user via a dialog if they want to reset and generate a new config
    when the configuration fails to load.
    Returns True if user confirmed, False otherwise.
    """
    popup_content = [
        "Failed to load project configuration.",
        ""
    ]
    if reason:
        clean_reason = str(reason).strip().replace("\n", " ")
        if len(clean_reason) > 60:
            clean_reason = clean_reason[:57] + "..."
        popup_content.append(f"Error: {clean_reason}")
        popup_content.append("")
    popup_content.extend([
        "Reset and generate a new config?",
        "",
        "---",
        "Reset configuration? [y/n]"
    ])

    popup_options = {
        'title': ' Reset Configuration ',
        'line': 0,
        'col': 0,
        'minwidth': 40,
        'maxwidth': 80,
        'padding': [1, 2, 1, 2],
        'border': [1, 1, 1, 1],
        'borderchars': ['─', '│', '─', '│', '╭', '╮', '╯', '╰'],
        'close': 'none',
        'zindex': 200,
    }

    confirmed = False
    try:
        popup_id = vim.eval(f"popup_create({json.dumps(popup_content)}, {popup_options})")
        vim.command("redraw!")
        try:
            answer_code = vim.eval('getchar()')
            try:
                answer_char = chr(int(answer_code))
            except (ValueError, TypeError):
                answer_char = str(answer_code) if answer_code else ""
            if answer_char.lower() == 'y':
                confirmed = True
        finally:
            if int(popup_id) > 0:
                vim.eval(f"popup_close({popup_id})")
                vim.command("redraw!")
    except Exception as e:
        util.log_info(f"Popup creation failed, falling back to confirm dialog: {e}")
        try:
            prompt_msg = "Failed to load project configuration. Reset and generate a new config?"
            res = vim.eval(f"confirm('{prompt_msg}', \"&Yes\\n&No\", 2)")
            confirmed = (int(res) == 1)
        except Exception as e2:
            util.log_info(f"Fallback confirm dialog failed: {e2}")
            confirmed = False

    return confirmed

def load_project_config_or_prompt(project_root=None, project_name=None):
    try:
        return load_project_data(project_name=project_name, start_dir=project_root)
    except Exception as e:
        util.log_info(f"Project configuration failed to load: {e}")
        if prompt_reset_config_dialog(reason=str(e)):
            new_data = create_default_project_data()
            if save_project_data(new_data, project_name=project_name, start_dir=project_root):
                util.display_message("Project configuration reset and generated anew.", history=True)
            else:
                util.display_message("Failed to save newly generated configuration.", error=True)
            return new_data
        raise

def config_command():
    """
    Shows a new buffer with guided project configuration options.
    """
    global _VIMINI_PENDING_PROJECT_CONFIG, _VIMINI_ORIGINAL_PROJECT_CONFIG
    global _VIMINI_CONFIG_PROJECT_ROOT, _VIMINI_CONFIG_PROJECT_NAME
    util.log_info("config_command()")
    try:
        current_path = os.path.realpath(vim.eval('getcwd()'))
        project_root = util.get_git_repo_root() or current_path
        project_name = util.get_git_repo_name() or get_project_name(project_root)

        try:
            data = load_project_config_or_prompt(project_root=project_root, project_name=project_name)
        except Exception:
            util.display_message("Project configuration loading aborted.", history=True)
            return

        config_dict = data.get("configuration", {})
        if not isinstance(config_dict, dict):
            config_dict = {}

        _VIMINI_ORIGINAL_PROJECT_CONFIG = dict(config_dict)
        _VIMINI_PENDING_PROJECT_CONFIG = dict(config_dict)
        _VIMINI_CONFIG_PROJECT_ROOT = project_root
        _VIMINI_CONFIG_PROJECT_NAME = project_name

        buffer_lines = _draw_config_listing(project_name, project_root, _VIMINI_PENDING_PROJECT_CONFIG, data)

        util.new_split()
        vim.command('file ViminiProjectConfig')
        buf = vim.current.buffer
        buf[:] = buffer_lines

        buf.options['buftype'] = 'nofile'
        buf.options['swapfile'] = False
        buf.options['modifiable'] = False
        buf.options['readonly'] = True
        buf.vars['vimini_config_root'] = project_root
        buf.vars['vimini_config_name'] = project_name

        # Syntax highlights
        vim.command("syntax match ViminiConfigKey '^\\s*[a-zA-Z0-9_-]\\+\\ze\\s*='")
        vim.command("syntax match ViminiConfigValue '=\\s*\\zs.*$'")
        vim.command("syntax match ViminiConfigComment '^\\s*#.*$'")
        vim.command("syntax match ViminiConfigHeader '^|.*'")
        vim.command("syntax match ViminiConfigSection '^>.*'")
        vim.command("syntax match ViminiConfigTree '^\\s*[│├└─].*$'")
        vim.command("highlight default link ViminiConfigKey Identifier")
        vim.command("highlight default link ViminiConfigValue String")
        vim.command("highlight default link ViminiConfigComment Comment")
        vim.command("highlight default link ViminiConfigHeader Comment")
        vim.command("highlight default link ViminiConfigTree SpecialComment")
        vim.command("highlight default link ViminiConfigSection Title")

        # Key mappings
        vim.command("nnoremap <buffer> <silent> <CR> :py3 from vimini.config import edit_config_option; edit_config_option()<CR>")
        vim.command("nnoremap <buffer> <silent> e :py3 from vimini.config import edit_config_option; edit_config_option()<CR>")
        vim.command("nnoremap <buffer> <silent> d :py3 from vimini.config import clear_config_option; clear_config_option()<CR>")
        vim.command("nnoremap <buffer> <silent> r :py3 from vimini.config import reset_config_options; reset_config_options()<CR>")
        vim.command("nnoremap <buffer> <silent> q :q<CR>")
        vim.command("autocmd BufUnload <buffer> :py3 from vimini.config import confirm_project_config; confirm_project_config()")

        # Place cursor on first configuration option
        vim.current.window.cursor = (8, 2)

    except Exception as e:
        util.display_message(f"Error opening project configuration: {e}", error=True)

def edit_config_option():
    global _VIMINI_PENDING_PROJECT_CONFIG
    try:
        buf = vim.current.buffer
        win = vim.current.window
        line_num, col = win.cursor
        line = buf[line_num - 1]
        key = _find_key_at_or_above(buf, line_num)

        if key and key.endswith("-command"):
            edit_config_as_json()
            return

        if not key:
            util.display_message("No editable configuration option selected on this line.")
            return

        schema_info = PROJECT_CONFIG_SCHEMA.get(key, {})
        schema_type = schema_info.get("type")
        current_val = _VIMINI_PENDING_PROJECT_CONFIG.get(key)

        if schema_type == "boolean":
            if isinstance(current_val, bool):
                cur_bool = current_val
            elif isinstance(current_val, str):
                cur_bool = current_val.strip().lower() in ("true", "1", "yes", "on")
            elif current_val is None:
                cur_bool = bool(schema_info.get("default", False))
            else:
                cur_bool = bool(current_val)

            new_bool = not cur_bool
            _VIMINI_PENDING_PROJECT_CONFIG[key] = new_bool
            _refresh_config_buffer(win, line_num, col)
            util.display_message(f"Set '{key}' to {repr(new_bool)}")
            return

        if schema_type == "choice" or key.endswith("-permission"):
            choices = schema_info.get("choices", ["Ask", "Allow", "Deny"])
            default_val = schema_info.get("default", "Ask")
            cur_choice = current_val if current_val is not None else default_val

            idx = -1
            for i, c in enumerate(choices):
                if str(cur_choice).strip().lower() == str(c).strip().lower():
                    idx = i
                    break

            if idx == -1:
                next_val = choices[0]
            else:
                next_val = choices[(idx + 1) % len(choices)]

            _VIMINI_PENDING_PROJECT_CONFIG[key] = next_val
            _refresh_config_buffer(win, line_num, col)
            util.display_message(f"Set '{key}' to {repr(next_val)}")
            return

        if isinstance(current_val, (dict, list)):
            edit_config_as_json()
            return

        if current_val is None:
            cur_str = ""
        else:
            cur_str = str(current_val)

        safe_prompt = f"Enter value for {key}: ".replace("'", "''")
        safe_default = cur_str.replace("'", "''")
        new_val = vim.eval(f"input('{safe_prompt}', '{safe_default}')")

        if new_val is None:
            return

        new_val_str = new_val.strip()
        if not new_val_str:
            _VIMINI_PENDING_PROJECT_CONFIG[key] = None
        else:
            if (new_val_str.startswith("{") and new_val_str.endswith("}")) or (new_val_str.startswith("[") and new_val_str.endswith("]")):
                try:
                    _VIMINI_PENDING_PROJECT_CONFIG[key] = json.loads(new_val_str)
                except Exception:
                    _VIMINI_PENDING_PROJECT_CONFIG[key] = new_val_str
            else:
                _VIMINI_PENDING_PROJECT_CONFIG[key] = new_val_str

        _refresh_config_buffer(win, line_num, col)
        val_display = repr(_VIMINI_PENDING_PROJECT_CONFIG[key]) if _VIMINI_PENDING_PROJECT_CONFIG[key] is not None else "(not set)"
        util.display_message(f"Set '{key}' to {val_display}")

    except Exception as e:
        util.display_message(f"Error editing configuration option: {e}", error=True)

def edit_config_as_json():
    global _VIMINI_PENDING_PROJECT_CONFIG
    global _JSON_EDITOR_STATE
    try:
        buf = vim.current.buffer
        win = vim.current.window
        line_num, col = win.cursor
        key = _find_key_at_or_above(buf, line_num)

        if not key:
            util.display_message("No editable configuration option selected on this line.")
            return

        config_buf_nr = buf.number
        current_val = _VIMINI_PENDING_PROJECT_CONFIG.get(key)
        if key.endswith("-command"):
            is_empty = False
            if current_val is None:
                is_empty = True
            elif isinstance(current_val, str) and not current_val.strip():
                is_empty = True
            elif isinstance(current_val, dict) and not current_val.get("command") and not current_val.get("alternatives"):
                is_empty = True

            if is_empty:
                default_cmd = "make" if key == "build-command" else "make test"
                default_desc = "Build the project" if key == "build-command" else "Run project tests"
                val_to_edit = {
                    "command": default_cmd,
                    "description": default_desc
                }
            elif isinstance(current_val, str):
                val_to_edit = {
                    "command": current_val,
                    "description": ""
                }
            elif isinstance(current_val, dict):
                val_to_edit = dict(current_val)
                if "command" in val_to_edit and "description" not in val_to_edit:
                    val_to_edit["description"] = ""
            elif isinstance(current_val, list):
                val_to_edit = list(current_val)
            else:
                val_to_edit = {
                    "command": str(current_val),
                    "description": ""
                }
        else:
            if current_val is None:
                val_to_edit = {}
            elif isinstance(current_val, (dict, list)):
                val_to_edit = current_val
            else:
                val_to_edit = current_val

        json_body = json.dumps(val_to_edit, indent=2) + "\n"
        initial_content = CONFIG_JSON_HELP_HEADER + "\n" + json_body

        with tempfile.NamedTemporaryFile(mode="w+", delete=False, encoding="utf-8", suffix=".config") as f:
            f.write(initial_content)
            tmp_filename = f.name

        _JSON_EDITOR_STATE = {
            "key": key,
            "tmp_filename": tmp_filename,
            "initial_content": initial_content,
            "last_error_content": None,
            "config_buf_nr": config_buf_nr,
            "config_line_num": line_num,
            "config_col": col,
        }

        util.new_split('horizontal')
        vim.command(f"silent edit {tmp_filename.replace(' ', '\\\\ ')}")
        json_buf = vim.current.buffer
        json_buf.options['filetype'] = 'config'
        json_buf.options['bufhidden'] = 'wipe'
        json_buf.vars['vimini_config_key'] = key
        json_buf.vars['vimini_config_tmp_file'] = tmp_filename
        json_buf.vars['vimini_config_buf_nr'] = int(config_buf_nr)
        vim.command("syntax match Comment '^\\s*#.*$'")
        vim.command("autocmd BufWritePost <buffer> py3 from vimini.config import finalize_json_config; finalize_json_config(is_wipeout=False)")
        vim.command("autocmd BufWipeout <buffer> py3 from vimini.config import finalize_json_config; finalize_json_config(is_wipeout=True)")

        json_line_num = 1
        for idx, l in enumerate(vim.current.buffer, 1):
            trimmed = l.strip()
            if trimmed and not trimmed.startswith('#'):
                json_line_num = idx
                break
        vim.current.window.cursor = (json_line_num, 0)
        vim.command("normal! zt")
        vim.command("redraw")
    except Exception as e:
        util.display_message(f"Error opening JSON editor: {e}", error=True)

def validate_config_json_content(key, content):
    try:
        parsed = json.loads(content)
    except Exception as err:
        return None, f"JSON syntax error: {err}"

    if key.endswith("-command"):
        if isinstance(parsed, str):
            cmd_str = parsed.strip()
            if not cmd_str:
                return None, "Command cannot be empty."
            parsed = {"command": cmd_str, "description": ""}
        elif not isinstance(parsed, (dict, list)):
            return None, "Command configuration must be a JSON object (or list of alternative commands)."

        if isinstance(parsed, dict):
            if "alternatives" in parsed:
                if not isinstance(parsed["alternatives"], list) or not parsed["alternatives"]:
                    return None, "'alternatives' must be a non-empty list of commands."
            elif "command" in parsed:
                cmd_str = str(parsed.get("command", "")).strip()
                if not cmd_str:
                    return None, "Command object must specify a non-empty 'command'."
                desc_str = str(parsed.get("description", "")).strip()
                if not desc_str:
                    return None, "Command object must specify a non-empty 'description'."
                if "options" in parsed:
                    if not isinstance(parsed["options"], list):
                        return None, "'options' must be a list of option objects."
            else:
                return None, "Command object must contain either 'command' or 'alternatives'."

    return parsed, None

def finalize_json_config(is_wipeout=True):
    global _VIMINI_PENDING_PROJECT_CONFIG
    global _JSON_EDITOR_STATE
    try:
        buf = vim.current.buffer
        key = ""
        tmp_filename = ""
        config_buf_nr = 0
        if hasattr(buf, 'vars'):
            try:
                key = _to_str(buf.vars.get("vimini_config_key"))
                tmp_filename = _to_str(buf.vars.get("vimini_config_tmp_file"))
                config_buf_nr = int(buf.vars.get("vimini_config_buf_nr", 0))
            except Exception:
                pass
        key = key or _JSON_EDITOR_STATE.get("key", "")
        tmp_filename = tmp_filename or _JSON_EDITOR_STATE.get("tmp_filename", "")
        config_buf_nr = config_buf_nr or _JSON_EDITOR_STATE.get("config_buf_nr", 0)
        saved_line = _JSON_EDITOR_STATE.get("config_line_num", 1)
        saved_col = _JSON_EDITOR_STATE.get("config_col", 1)
        if not key or not tmp_filename or not os.path.exists(tmp_filename):
            return

        with open(tmp_filename, "r", encoding="utf-8") as f:
            raw_content = f.read()

        initial_content = _JSON_EDITOR_STATE.get("initial_content")
        if initial_content is not None and raw_content.strip() == initial_content.strip():
            if is_wipeout:
                try:
                    os.remove(tmp_filename)
                except Exception:
                    pass
                _JSON_EDITOR_STATE["last_error_content"] = None
            return

        last_error_content = _JSON_EDITOR_STATE.get("last_error_content")
        if last_error_content is not None and raw_content.strip() == last_error_content.strip():
            if is_wipeout:
                try:
                    os.remove(tmp_filename)
                except Exception:
                    pass
                _JSON_EDITOR_STATE["last_error_content"] = None
                util.display_message("Configuration changes abandoned.", history=True)
            return

        clean_lines = [line for line in raw_content.splitlines(keepends=True) if not line.strip().startswith('#')]
        content = "".join(clean_lines).strip()

        if not content:
            if is_wipeout:
                try:
                    os.remove(tmp_filename)
                except Exception:
                    pass
                _JSON_EDITOR_STATE["last_error_content"] = None
            return

        parsed, err_msg = validate_config_json_content(key, content)
        if err_msg is not None:
            if not is_wipeout:
                util.display_message(f"Configuration error: {err_msg}", error=True)
                return

            error_marker = "# === CONFIGURATION VALIDATION ERROR ==="
            if error_marker in raw_content:
                base_raw = raw_content[:raw_content.index(error_marker)].rstrip()
            else:
                base_raw = raw_content.rstrip()

            error_comment_block = (
                f"\n\n{error_marker}\n"
                f"# Error: {err_msg}\n"
                "#\n"
                "# To fix: edit the configuration above, save (:w) and quit (:q).\n"
                "# To abandon: quit without making any changes (:q or :q!).\n"
                "# ======================================\n"
            )
            reopen_content = base_raw + error_comment_block
            with open(tmp_filename, "w", encoding="utf-8") as f:
                f.write(reopen_content)

            _JSON_EDITOR_STATE["last_error_content"] = reopen_content
            _JSON_EDITOR_STATE["key"] = key
            _JSON_EDITOR_STATE["tmp_filename"] = tmp_filename
            _JSON_EDITOR_STATE["config_buf_nr"] = config_buf_nr
            _JSON_EDITOR_STATE["config_line_num"] = saved_line
            _JSON_EDITOR_STATE["config_col"] = saved_col

            vim.command("call timer_start(50, { -> execute('py3 from vimini.config import reopen_json_editor; reopen_json_editor()') })")
            return

        _VIMINI_PENDING_PROJECT_CONFIG[key] = parsed
        if is_wipeout:
            try:
                os.remove(tmp_filename)
            except Exception:
                pass
            _JSON_EDITOR_STATE["last_error_content"] = None

        util.display_message(f"Updated configuration for '{key}'.", history=True)
        _refresh_config_buffer(line_num=saved_line, col=saved_col, buf_nr=config_buf_nr)
    except Exception as e:
        util.display_message(f"Error saving JSON configuration: {e}", error=True)

def reopen_json_editor():
    global _JSON_EDITOR_STATE
    try:
        key = _JSON_EDITOR_STATE.get("key", "")
        tmp_filename = _JSON_EDITOR_STATE.get("tmp_filename", "")
        config_buf_nr = _JSON_EDITOR_STATE.get("config_buf_nr", 0)
        saved_line = _JSON_EDITOR_STATE.get("config_line_num", 1)
        saved_col = _JSON_EDITOR_STATE.get("config_col", 1)
        if not key or not tmp_filename or not os.path.exists(tmp_filename):
            return

        util.new_split('horizontal')
        vim.command(f"silent edit {tmp_filename.replace(' ', '\\\\ ')}")
        json_buf = vim.current.buffer
        json_buf.options['filetype'] = 'markdown'
        json_buf.options['bufhidden'] = 'wipe'
        json_buf.vars['vimini_config_key'] = key
        json_buf.vars['vimini_config_tmp_file'] = tmp_filename
        json_buf.vars['vimini_config_buf_nr'] = int(config_buf_nr)
        vim.command("syntax match Comment '^\\s*#.*$'")
        vim.command("autocmd BufWritePost <buffer> py3 from vimini.config import finalize_json_config; finalize_json_config(is_wipeout=False)")
        vim.command("autocmd BufWipeout <buffer> py3 from vimini.config import finalize_json_config; finalize_json_config(is_wipeout=True)")

        json_line_num = 1
        for idx, l in enumerate(vim.current.buffer, 1):
            trimmed = l.strip()
            if trimmed and not trimmed.startswith('#'):
                json_line_num = idx
                break
        vim.current.window.cursor = (json_line_num, 0)
        vim.command("normal! zt")
        vim.command("redraw")
    except Exception as e:
        util.display_message(f"Error reopening JSON editor: {e}", error=True)

def clear_config_option():
    global _VIMINI_PENDING_PROJECT_CONFIG
    try:
        buf = vim.current.buffer
        win = vim.current.window
        line_num, col = win.cursor
        key = _find_key_at_or_above(buf, line_num)

        if not key:
            util.display_message("No configuration option selected on this line.")
            return

        _VIMINI_PENDING_PROJECT_CONFIG[key] = None
        _refresh_config_buffer(win, line_num, col)
        util.display_message(f"Cleared '{key}'.")

    except Exception as e:
        util.display_message(f"Error clearing configuration option: {e}", error=True)

def reset_config_options():
    global _VIMINI_PENDING_PROJECT_CONFIG, _VIMINI_ORIGINAL_PROJECT_CONFIG
    try:
        if _VIMINI_ORIGINAL_PROJECT_CONFIG is None:
            return
        _VIMINI_PENDING_PROJECT_CONFIG = dict(_VIMINI_ORIGINAL_PROJECT_CONFIG)
        win = vim.current.window
        line_num, col = win.cursor
        _refresh_config_buffer(win, line_num, col)
        util.display_message("Reset configuration options to last saved state.")
    except Exception as e:
        util.display_message(f"Error resetting configuration options: {e}", error=True)

def _refresh_config_buffer(win=None, line_num=None, col=None, buf_nr=None):
    target_buf = None
    if buf_nr:
        try:
            target_buf_nr = int(buf_nr)
            for b in vim.buffers:
                if b.number == target_buf_nr:
                    target_buf = b
                    break
        except Exception:
            pass

    if target_buf is None and win is not None:
        try:
            target_buf = win.buffer
        except Exception:
            pass

    if target_buf is None:
        for b in vim.buffers:
            if b.name and os.path.basename(b.name) == "ViminiProjectConfig":
                target_buf = b
                break

    if target_buf is None:
        target_buf = vim.current.buffer

    project_root = _VIMINI_CONFIG_PROJECT_ROOT
    if not project_root and target_buf is not None and hasattr(target_buf, 'vars'):
        try:
            project_root = _to_str(target_buf.vars.get("vimini_config_root"))
        except Exception:
            pass

    project_name = _VIMINI_CONFIG_PROJECT_NAME
    if not project_name and target_buf is not None and hasattr(target_buf, 'vars'):
        try:
            project_name = _to_str(target_buf.vars.get("vimini_config_name"))
        except Exception:
            pass

    try:
        data = load_project_data(start_dir=project_root)
    except Exception:
        data = create_default_project_data()
    buffer_lines = _draw_config_listing(project_name, project_root, _VIMINI_PENDING_PROJECT_CONFIG, data)

    target_buf.options['readonly'] = False
    target_buf.options['modifiable'] = True
    try:
        target_buf[:] = buffer_lines
    finally:
        target_buf.options['modifiable'] = False
        target_buf.options['readonly'] = True

    for w in vim.windows:
        if w.buffer.number == target_buf.number:
            if line_num is not None and col is not None:
                try:
                    w.cursor = (min(line_num, len(buffer_lines)), col)
                except vim.error:
                    pass

    vim.command("redraw")

def confirm_project_config():
    global _VIMINI_PENDING_PROJECT_CONFIG, _VIMINI_ORIGINAL_PROJECT_CONFIG
    global _VIMINI_CONFIG_PROJECT_ROOT, _VIMINI_CONFIG_PROJECT_NAME
    try:
        if _VIMINI_PENDING_PROJECT_CONFIG is None or _VIMINI_ORIGINAL_PROJECT_CONFIG is None:
            return

        if _VIMINI_PENDING_PROJECT_CONFIG == _VIMINI_ORIGINAL_PROJECT_CONFIG:
            return

        project_root = _VIMINI_CONFIG_PROJECT_ROOT or util.get_git_repo_root() or os.getcwd()

        popup_content = ["Save project configuration changes?", ""]
        for k, v in _VIMINI_PENDING_PROJECT_CONFIG.items():
            orig_v = _VIMINI_ORIGINAL_PROJECT_CONFIG.get(k)
            if orig_v != v:
                orig_disp = json.dumps(orig_v) if isinstance(orig_v, (dict, list)) else (orig_v if orig_v is not None else "(not set)")
                new_disp = json.dumps(v) if isinstance(v, (dict, list)) else (v if v is not None else "(not set)")
                popup_content.append(f"  {k}: {orig_disp} -> {new_disp}")

        popup_content.extend(['', '---', 'Accept changes? [y/n]'])

        popup_options = {
            'title': ' Confirm Configuration ', 'line': 0, 'col': 0,
            'minwidth': 40, 'maxwidth': 80,
            'padding': [1, 2, 1, 2], 'border': [1, 1, 1, 1],
            'borderchars': ['─', '│', '─', '│', '╭', '╮', '╯', '╰'],
            'close': 'none', 'zindex': 200,
        }
        popup_id = vim.eval(f"popup_create({json.dumps(popup_content)}, {popup_options})")
        vim.command("redraw!")

        confirmed = False
        try:
            answer_code = vim.eval('getchar()')
            answer_char = chr(int(answer_code))
            if answer_char.lower() == 'y':
                confirmed = True
        except (vim.error, ValueError, TypeError):
            pass
        finally:
            vim.eval(f"popup_close({popup_id})")
            vim.command("redraw!")

        if confirmed:
            try:
                data = load_project_data(start_dir=project_root)
            except Exception:
                data = create_default_project_data()
            data["configuration"] = _VIMINI_PENDING_PROJECT_CONFIG
            if save_project_data(data, start_dir=project_root):
                util.display_message("Project configuration updated and saved.", history=True)
            else:
                util.display_message("Failed to save project configuration.", error=True)
        else:
            util.display_message("Project configuration changes discarded.", history=True)

    except Exception as e:
        util.display_message(f"Error confirming project configuration: {e}", error=True)
    finally:
        _VIMINI_PENDING_PROJECT_CONFIG = None
        _VIMINI_ORIGINAL_PROJECT_CONFIG = None
        _VIMINI_CONFIG_PROJECT_ROOT = None
        _VIMINI_CONFIG_PROJECT_NAME = None
