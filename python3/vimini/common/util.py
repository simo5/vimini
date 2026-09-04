# Vimini Agent Package
# Common utility module for vimini without vim dependency.
import os
import json
import subprocess
import re
import shlex
import difflib

PROJECTS_DIR = os.path.expanduser("~/.var/vimini/projects")
CURRENT_PROJECT_DATA_VERSION = "0.1"

PROJECT_CONFIG_SCHEMA = {
    "build-command": {
        "label": "Build Command",
        "description": "Shell command, alternative commands, or command configuration with options to compile/build the project",
        "default": None,
        "type": "string"
    },
    "build-permission": {
        "label": "Build Permission",
        "description": "Execution permission for build command (Allow, Deny, or Ask)",
        "default": "Ask",
        "type": "choice",
        "choices": ["Ask", "Allow", "Deny"]
    },
    "test-command": {
        "label": "Test Command",
        "description": "Shell command, alternative commands, or command configuration with options to run the project test suite",
        "default": None,
        "type": "string"
    },
    "test-permission": {
        "label": "Test Permission",
        "description": "Execution permission for test command (Allow, Deny, or Ask)",
        "default": "Ask",
        "type": "choice",
        "choices": ["Ask", "Allow", "Deny"]
    },
    "compilation-needed": {
        "label": "Compilation Needed",
        "description": "Whether the project requires compilation (true/false, default false)",
        "default": False,
        "type": "boolean"
    }
}

def get_git_repo_root(start_dir=None):
    """
    Finds the root directory of the git repository starting from start_dir or current working directory.
    Does not require vim.
    """
    if start_dir is None:
        start_dir = os.getcwd()
    try:
        res = subprocess.run(
            ['git', '-C', start_dir, 'rev-parse', '--show-toplevel'],
            capture_output=True,
            text=True,
            check=False
        )
        if res.returncode == 0 and res.stdout.strip():
            return os.path.realpath(res.stdout.strip())
    except Exception:
        pass
    return None

def get_project_root(start_dir=None):
    """
    Returns the repository root path if inside a git repo, otherwise the absolute path of start_dir or cwd.
    """
    if start_dir is None:
        start_dir = os.getcwd()
    repo_root = get_git_repo_root(start_dir)
    if repo_root:
        return repo_root
    return os.path.realpath(start_dir)

def get_project_name(start_dir=None):
    """
    Returns the project name based on the git repository directory name or current working directory.
    """
    if start_dir is None:
        start_dir = os.getcwd()
    repo_root = get_git_repo_root(start_dir)
    if repo_root:
        return os.path.basename(repo_root)
    name = os.path.basename(os.path.realpath(start_dir))
    return name if name else "temp"

def get_project_data_file_path(project_name=None, start_dir=None):
    """
    Returns the path to the project data JSON file in ~/.var/vimini/projects.
    """
    if not project_name:
        project_name = get_project_name(start_dir)
    if not project_name or project_name == "temp":
        return None
    return os.path.join(PROJECTS_DIR, project_name)

def create_default_project_data():
    """
    Returns a new default project data dictionary.
    """
    config = {k: v.get("default") for k, v in PROJECT_CONFIG_SCHEMA.items()}
    return {
        "version": CURRENT_PROJECT_DATA_VERSION,
        "configuration": config,
        "files": []
    }

def _parse_version(v):
    if not isinstance(v, str):
        return (0,)
    try:
        return tuple(int(x) for x in v.strip().split('.'))
    except Exception:
        return (0,)

def upgrade_project_data(raw_data):
    """
    Upgrades project data to the current format ("0.1").
    Migrates legacy list-based context file lists to the 'files' section.
    If raw_data is a dict with a version higher than CURRENT_PROJECT_DATA_VERSION,
    raises a ValueError.
    """
    default = create_default_project_data()
    if isinstance(raw_data, list):
        default["files"] = raw_data
        return default
    elif isinstance(raw_data, dict):
        version = raw_data.get("version", CURRENT_PROJECT_DATA_VERSION)
        if _parse_version(version) > _parse_version(CURRENT_PROJECT_DATA_VERSION):
            raise ValueError(f"Project configuration version '{version}' is higher than supported version '{CURRENT_PROJECT_DATA_VERSION}'.")

        config = raw_data.get("configuration", {})
        if not isinstance(config, dict):
            config = {}
        for k, schema_item in PROJECT_CONFIG_SCHEMA.items():
            if k not in config:
                config[k] = schema_item.get("default")
        # Normalize single-string commands to dict with description
        for cmd_key in ("build-command", "test-command"):
            cmd_val = config.get(cmd_key)
            if isinstance(cmd_val, str) and cmd_val.strip():
                if cmd_val.strip().startswith("{"):
                    try:
                        p = json.loads(cmd_val)
                        if isinstance(p, dict):
                            if "description" not in p:
                                p["description"] = ""
                            config[cmd_key] = p
                            continue
                    except Exception:
                        pass
                config[cmd_key] = {
                    "command": cmd_val.strip(),
                    "description": ""
                }
        files = raw_data.get("files", [])
        if not isinstance(files, list):
            files = []
        return {
            "version": CURRENT_PROJECT_DATA_VERSION,
            "configuration": config,
            "files": files
        }
    raise ValueError(f"Invalid project configuration format: expected dict or list, got {type(raw_data).__name__}")

def load_project_data(project_name=None, start_dir=None):
    file_path = get_project_data_file_path(project_name, start_dir)
    if not file_path or not os.path.exists(file_path):
        return create_default_project_data()
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            raw_data = json.load(f)
        data = upgrade_project_data(raw_data)
        # Save if format was upgraded
        if isinstance(raw_data, list) or (isinstance(raw_data, dict) and raw_data.get("version") != CURRENT_PROJECT_DATA_VERSION):
            save_project_data(data, project_name, start_dir)
        return data
    except Exception:
        raise

def save_project_data(data, project_name=None, start_dir=None):
    file_path = get_project_data_file_path(project_name, start_dir)
    if not file_path:
        return False
    try:
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
        return True
    except Exception:
        return False

def get_project_config(key, project_name=None, start_dir=None, default=None):
    try:
        data = load_project_data(project_name, start_dir)
    except Exception:
        return default
    config = data.get("configuration", {})
    if isinstance(config, dict):
        val = config.get(key)
        if val is None:
            schema_default = PROJECT_CONFIG_SCHEMA.get(key, {}).get("default")
            if schema_default is not None:
                val = schema_default
            elif key.endswith("-permission"):
                val = "Ask"
            else:
                val = default

        schema_info = PROJECT_CONFIG_SCHEMA.get(key, {})
        if schema_info.get("type") == "boolean" and val is not None:
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.strip().lower() in ("true", "1", "yes", "on")
            return bool(val)
        return val
    return default

def set_project_config(key, value, project_name=None, start_dir=None):
    data = load_project_data(project_name, start_dir)
    data["configuration"][key] = value
    return save_project_data(data, project_name, start_dir)

def parse_tool_command_config(raw_config):
    """
    Parses raw configuration for build or test commands into a normalized structure.
    Supports:
    - Simple command string (e.g. "cargo build")
    - Alternative commands list (e.g. ["make debug", "make release"] or list of dicts)
    - Dict specifying alternatives: {"alternatives": [...]}
    - Dict specifying base command and options/arguments:
      {
        "command": "pytest",
        "description": "Run pytest suite",
        "options": [
          {"name": "verbose", "flag": "-v", "type": "option", "has_value": false, "description": "Verbose output"},
          {"name": "keyword", "flag": "-k", "type": "option", "has_value": true, "description": "Keyword filter"},
          {"name": "mode", "flag": "--mode", "type": "option", "choices": ["fast", "slow"], "description": "Speed mode"},
          {"name": "test_path", "type": "argument", "description": "Target test file"}
        ]
      }
    """
    if raw_config is None:
        return None

    if isinstance(raw_config, str):
        stripped = raw_config.strip()
        if not stripped:
            return None
        if stripped.startswith(('{', '[')):
            try:
                parsed = json.loads(stripped)
                return parse_tool_command_config(parsed)
            except Exception:
                pass
        return {
            "type": "simple",
            "command": stripped,
            "description": f"Shell command: {stripped}"
        }

    if isinstance(raw_config, list):
        alts = []
        for item in raw_config:
            if isinstance(item, str):
                cmd = item.strip()
                alts.append({
                    "command": cmd,
                    "name": cmd,
                    "description": f"Execute: {cmd}"
                })
            elif isinstance(item, dict):
                cmd = str(item.get("command", "")).strip()
                name = str(item.get("name", cmd)).strip()
                desc = str(item.get("description", f"Execute: {cmd}")).strip()
                alts.append({
                    "command": cmd,
                    "name": name,
                    "description": desc
                })
        return {
            "type": "alternatives",
            "description": "Select one of the configured alternative commands (all-or-nothing)",
            "alternatives": alts
        }

    if isinstance(raw_config, dict):
        if "alternatives" in raw_config and isinstance(raw_config["alternatives"], list):
            alts = []
            for item in raw_config["alternatives"]:
                if isinstance(item, str):
                    cmd = item.strip()
                    alts.append({
                        "command": cmd,
                        "name": cmd,
                        "description": f"Execute: {cmd}"
                    })
                elif isinstance(item, dict):
                    cmd = str(item.get("command", "")).strip()
                    name = str(item.get("name", cmd)).strip()
                    desc = str(item.get("description", f"Execute: {cmd}")).strip()
                    alts.append({
                        "command": cmd,
                        "name": name,
                        "description": desc
                    })
            return {
                "type": "alternatives",
                "description": str(raw_config.get("description", "Select one of the configured alternative commands (all-or-nothing)")),
                "alternatives": alts
            }

        base_cmd = str(raw_config.get("command", "")).strip()
        desc = str(raw_config.get("description", ""))
        raw_items = []
        if "options" in raw_config and isinstance(raw_config["options"], list):
            raw_items.extend(raw_config["options"])
        if "arguments" in raw_config and isinstance(raw_config["arguments"], list):
            for a in raw_config["arguments"]:
                if isinstance(a, dict) and "type" not in a:
                    a = dict(a)
                    a["type"] = "argument"
                raw_items.append(a)

        normalized_items = []
        for idx, item in enumerate(raw_items):
            if isinstance(item, str):
                if item.startswith("-"):
                    item_name = item.lstrip("-").replace("-", "_")
                    item = {"name": item_name, "flag": item, "type": "option", "has_value": False}
                else:
                    item = {"name": item.replace("-", "_"), "type": "argument"}
            if not isinstance(item, dict):
                continue

            opt_type = item.get("type")
            flag = item.get("flag")
            if not opt_type:
                opt_type = "option" if flag else "argument"

            name = item.get("name")
            if not name:
                if flag:
                    name = flag.lstrip("-").replace("-", "_")
                else:
                    name = f"arg_{idx+1}"

            clean_name = re.sub(r'[^a-zA-Z0-9_]', '_', name)
            if clean_name and clean_name[0].isdigit():
                clean_name = f"arg_{clean_name}"
            if not clean_name:
                clean_name = f"opt_{idx+1}"

            choices = item.get("choices")
            if choices and isinstance(choices, list):
                choices = [str(c) for c in choices]
            else:
                choices = None

            opt_desc = str(item.get("description", ""))

            if opt_type == "option":
                if not flag:
                    flag = f"--{name}"
                has_val = item.get("has_value")
                if has_val is None:
                    has_val = item.get("takes_value")
                if has_val is None:
                    has_val = True if (choices or item.get("value_type") == "string") else False

                normalized_items.append({
                    "name": clean_name,
                    "raw_name": name,
                    "type": "option",
                    "flag": flag,
                    "description": opt_desc,
                    "has_value": bool(has_val),
                    "choices": choices,
                    "required": bool(item.get("required", False))
                })
            else:
                normalized_items.append({
                    "name": clean_name,
                    "raw_name": name,
                    "type": "argument",
                    "description": opt_desc,
                    "choices": choices,
                    "required": bool(item.get("required", False))
                })

        return {
            "type": "command_with_options",
            "command": base_cmd,
            "description": desc,
            "options": normalized_items
        }

    return None

def get_project_tool_config(tool_label, project_name=None, start_dir=None):
    """
    Returns the parsed configuration for tool_label ('build' or 'test').
    Checks:
    1. {tool_label}-command
    2. {tool_label}-commands
    3. {tool_label}-alternatives
    4. {tool_label}-options
    """
    try:
        data = load_project_data(project_name, start_dir)
    except Exception:
        return None

    config = data.get("configuration", {})
    if not isinstance(config, dict):
        return None

    cmd_val = config.get(f"{tool_label}-command")
    if cmd_val is None:
        cmd_val = config.get(f"{tool_label}-commands")

    alts_val = config.get(f"{tool_label}-alternatives")
    if alts_val is not None:
        if isinstance(alts_val, list):
            return parse_tool_command_config({"alternatives": alts_val})
        elif isinstance(alts_val, dict):
            return parse_tool_command_config(alts_val)

    opts_val = config.get(f"{tool_label}-options")
    if opts_val is not None and isinstance(cmd_val, str):
        return parse_tool_command_config({
            "command": cmd_val,
            "options": opts_val if isinstance(opts_val, list) else [opts_val]
        })

    if cmd_val is not None:
        return parse_tool_command_config(cmd_val)

    return None

def validate_tool_call(tool_label, args_dict, tool_config):
    """
    Validates args_dict against tool_config.
    Returns (is_valid: bool, result_or_error: str).
    If is_valid is True, result_or_error is the composed command string.
    If is_valid is False, result_or_error is the determinable error explanation for the AI.
    """
    if not tool_config:
        return False, f"No {tool_label} command is configured for this project."

    args_dict = args_dict or {}

    # Case 1: Simple command (no options accepted)
    if tool_config["type"] == "simple":
        non_empty = {k: v for k, v in args_dict.items() if v is not None and v is not False and v != ""}
        if non_empty:
            return False, f"The configured {tool_label} command '{tool_config['command']}' accepts no options or arguments, but received: {list(non_empty.keys())}."
        return True, tool_config["command"]

    # Case 2: Alternative commands (all-or-nothing, select one)
    if tool_config["type"] == "alternatives":
        allowed_alts = tool_config.get("alternatives", [])
        valid_commands = [alt["command"] for alt in allowed_alts]
        valid_names = [alt["name"] for alt in allowed_alts if alt.get("name")]

        unexpected = [k for k in args_dict.keys() if k not in ("command", "selected_command", "cmd", "name")]
        if unexpected:
            return False, (
                f"Alternative commands are all-or-nothing and do not accept additional options or flags. "
                f"Unexpected parameter(s): {unexpected}. "
                f"Allowed alternative commands: {valid_commands}."
            )

        selected = None
        for k in ("command", "selected_command", "cmd", "name"):
            if k in args_dict and args_dict[k]:
                selected = str(args_dict[k]).strip()
                break

        if not selected:
            return False, (
                f"No command selected. You must select one of the following alternative commands via the 'command' parameter: "
                f"{valid_commands}."
            )

        matched_cmd = None
        for alt in allowed_alts:
            if selected == alt["command"] or (alt.get("name") and selected == alt["name"]):
                matched_cmd = alt["command"]
                break

        if not matched_cmd:
            return False, (
                f"'{selected}' is not one of the allowed alternative commands. "
                f"Allowed choices are: {valid_commands}."
            )

        return True, matched_cmd

    # Case 3: Command with options and arguments
    if tool_config["type"] == "command_with_options":
        base_cmd = tool_config.get("command", "")
        configured_items = tool_config.get("options", [])
        name_map = {item["name"]: item for item in configured_items}
        raw_name_map = {item["raw_name"]: item for item in configured_items if item.get("raw_name")}
        flag_map = {item["flag"]: item for item in configured_items if item.get("flag")}

        errors = []
        unrecognized = []
        processed_args = {}

        # Flatten args_dict in case AI passes nested 'options' or 'arguments'
        flat_args = {}
        for k, v in args_dict.items():
            if k in ("options", "arguments", "args") and isinstance(v, dict):
                flat_args.update(v)
            elif k in ("flags", "options") and isinstance(v, list):
                for f in v:
                    if isinstance(f, str):
                        flat_args[f] = True
            else:
                flat_args[k] = v

        for k, v in flat_args.items():
            item = name_map.get(k) or raw_name_map.get(k) or flag_map.get(k)
            if not item:
                unrecognized.append(k)
                continue
            processed_args[item["name"]] = (item, v)

        if unrecognized:
            allowed_names = sorted(list(set(list(name_map.keys()) + list(flag_map.keys()))))
            errors.append(f"Unrecognized parameter(s): {unrecognized}. Allowed parameters: {allowed_names}.")

        # Check required arguments
        for item in configured_items:
            if item.get("required") and item["name"] not in processed_args:
                errors.append(f"Missing required argument '{item['name']}' ({item.get('description', '')}).")

        options_to_apply = []
        arguments_to_apply = []

        for item in configured_items:
            name = item["name"]
            if name not in processed_args:
                continue
            _, val = processed_args[name]
            if val is None:
                continue

            if item["type"] == "option":
                flag = item["flag"]
                has_value = item["has_value"]
                if not has_value:
                    if isinstance(val, bool):
                        if val:
                            options_to_apply.append((item, None))
                    elif isinstance(val, str) and val.lower() in ("true", "1", "yes"):
                        options_to_apply.append((item, None))
                    elif isinstance(val, str) and val.lower() in ("false", "0", "no", ""):
                        pass
                    elif val in (1,):
                        options_to_apply.append((item, None))
                    elif val in (0,):
                        pass
                    else:
                        errors.append(
                            f"Option '{name}' (flag '{flag}') takes no value (boolean flag). "
                            f"Expected boolean (true/false), but received: {repr(val)}."
                        )
                else:
                    if isinstance(val, (dict, list)):
                        errors.append(
                            f"Value for option '{name}' must be a single command line argument string, "
                            f"not a {type(val).__name__}."
                        )
                        continue

                    val_str = str(val)
                    if "\n" in val_str or "\r" in val_str:
                        errors.append(f"Value for option '{name}' cannot contain newline characters.")
                        continue

                    choices = item.get("choices")
                    if choices and val_str not in choices:
                        errors.append(
                            f"Invalid value {repr(val_str)} for option '{name}'. "
                            f"Allowed choices are: {choices}."
                        )
                        continue

                    options_to_apply.append((item, val_str))
            else:
                # argument
                if isinstance(val, (dict, list)):
                    errors.append(
                        f"Value for argument '{name}' must be a single command line argument string, "
                        f"not a {type(val).__name__}."
                    )
                    continue

                val_str = str(val)
                if "\n" in val_str or "\r" in val_str:
                    errors.append(f"Value for argument '{name}' cannot contain newline characters.")
                    continue

                choices = item.get("choices")
                if choices and val_str not in choices:
                    errors.append(
                        f"Invalid value {repr(val_str)} for argument '{name}'. "
                        f"Allowed choices are: {choices}."
                    )
                    continue

                arguments_to_apply.append((item, val_str))

        if errors:
            err_msg = "Command rejected due to schema validation errors:\n" + "\n".join(f"- {e}" for e in errors)
            return False, err_msg

        # Compose command line
        composed_parts = [base_cmd] if base_cmd else []
        for item, val_str in options_to_apply:
            flag = item["flag"]
            if val_str is None:
                composed_parts.append(flag)
            else:
                if flag.endswith("="):
                    composed_parts.append(f"{flag}{shlex.quote(val_str)}")
                else:
                    composed_parts.append(f"{flag} {shlex.quote(val_str)}")

        for item, val_str in arguments_to_apply:
            composed_parts.append(shlex.quote(val_str))

        composed_cmd = " ".join(composed_parts).strip()
        return True, composed_cmd

    return False, f"Unknown configuration type for {tool_label} command."

def get_relative_path(file_path, repo_name=None, git_root=None):
    """
    Computes a path for a file relative to its git repository root,
    or to the user's home directory as a fallback.
    Prepends the capitalized git repo name or 'HOME' to the path.
    """
    if not file_path:
        return ""

    abs_path = os.path.realpath(file_path)

    if git_root and abs_path.startswith(git_root):
        relative_path = os.path.relpath(abs_path, git_root)
        if not repo_name:
            repo_name = os.path.basename(git_root)
        if repo_name:
            return f"{repo_name.upper()}:{relative_path}"
        return relative_path

    home_dir = os.path.expanduser('~')
    # Check if the path is inside the home directory.
    if abs_path.startswith(home_dir):
        try:
            relative_path = os.path.relpath(abs_path, home_dir)
            return f"HOME:{relative_path}"
        except ValueError:
            # This can happen on Windows if home_dir and abs_path are on different drives,
            # even with startswith check if symlinks are involved. Fallback is safe.
            pass

    # Fallback for files not in git repo or home, or on different drives on Windows.
    return os.path.basename(abs_path)

def list_directory(directory_path=".", project_root=None):
    try:
        if not project_root:
            project_root = get_project_root()
        else:
            project_root = os.path.realpath(project_root)
        target_path = os.path.realpath(os.path.join(project_root, directory_path))

        try:
            if os.path.commonpath([project_root, target_path]) != project_root:
                return "Security error: Cannot list directories above the project directory."
        except ValueError:
            return "Security error: Path resolution failed or invalid cross-drive path."

        if not os.path.exists(target_path):
            return f"Error: Directory '{directory_path}' does not exist."
        if not os.path.isdir(target_path):
            return f"Error: Path '{directory_path}' is not a directory."

        entries = os.listdir(target_path)
        res = []
        for entry in sorted(entries):
            full = os.path.join(target_path, entry)
            if os.path.isdir(full):
                res.append(f"{entry}/")
            else:
                res.append(entry)
        return "\n".join(res)
    except Exception as e:
        return f"Error listing directory: {e}"

def read_file(filepath, project_root=None):
    try:
        if not project_root:
            project_root = get_project_root()
        else:
            project_root = os.path.realpath(project_root)
        target_path = os.path.realpath(os.path.join(project_root, filepath))

        try:
            if os.path.commonpath([project_root, target_path]) != project_root:
                return "Security error: Cannot read files above the project directory."
        except ValueError:
            return "Security error: Path resolution failed or invalid cross-drive path."

        if not os.path.exists(target_path):
            return f"Error: File '{filepath}' does not exist."
        if not os.path.isfile(target_path):
            return f"Error: Path '{filepath}' is not a regular file."

        with open(target_path, 'r', encoding='utf-8', errors='replace') as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"

def generate_diff_for_file(file_path, file_content, project_root=None):
    """
    Generates a unified diff comparing the existing file on disk with file_content.
    Returns (diff_text, error_message).
    - If there is an error, diff_text is None and error_message is a string.
    - If there are no changes, diff_text is "" and error_message is None.
    - If there are changes, diff_text is a unified diff string and error_message is None.
    """
    if not file_path:
        return None, "Missing file path."

    if not project_root:
        project_root = get_project_root()
    else:
        project_root = os.path.realpath(project_root)

    if os.path.isabs(file_path):
        target_path = os.path.realpath(file_path)
    else:
        target_path = os.path.realpath(os.path.join(project_root, file_path))

    try:
        if os.path.commonpath([project_root, target_path]) != project_root:
            return None, f"Security error: Cannot modify files outside project directory: {file_path}"
    except ValueError:
        return None, f"Security error: Path resolution failed for {file_path}."

    if os.path.exists(target_path) and os.path.isdir(target_path):
        return None, f"Target path '{file_path}' is a directory, not a regular file."

    relative_path = os.path.relpath(target_path, project_root).replace(os.sep, '/')
    file_exists = os.path.exists(target_path)

    original_content = ""
    if file_exists:
        try:
            with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                original_content = f.read()
        except Exception as e:
            return None, f"Error reading existing file '{file_path}': {e}"

    if file_content is None:
        file_content = ""

    orig_lines = original_content.splitlines()
    new_lines = file_content.splitlines()

    if file_exists and orig_lines == new_lines:
        return "", None

    from_path = f"a/{relative_path}" if file_exists else "/dev/null"
    to_path = f"b/{relative_path}"

    diff_lines = list(difflib.unified_diff(
        orig_lines,
        new_lines,
        fromfile=from_path,
        tofile=to_path,
        lineterm=""
    ))

    if not diff_lines:
        return "", None

    diff_header = f"diff --git a/{relative_path} b/{relative_path}"
    diff_text = "\n".join([diff_header] + diff_lines) + "\n"
    return diff_text, None
