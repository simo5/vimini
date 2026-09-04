import os
import json
import queue
import subprocess
import logging
import tempfile
import sys
from google import genai
from google.genai import types
from vimini.common.util import (
    get_project_root,
    get_project_name,
    get_project_config,
    get_project_data_file_path,
    get_project_tool_config,
    validate_tool_call,
    list_directory,
    parse_tool_command_config,
    read_file,
    generate_diff_for_file
)
from vimini.agent.comms import CommSession
from vimini.common.genai import get_client, load_api_key, create_generation_config

logger = logging.getLogger('vimini_agent')

def generate_tool_declaration_schema(tool_label, tool_config):
    """
    Generates (description, parameters_schema) for build_code or test_code
    based on the parsed tool_config.
    """
    tool_action = "Compiles or builds the code in the project" if tool_label == "build" else "Runs the project test suite"

    if not tool_config:
        return (
            f"{tool_action}. (No {tool_label} command is currently configured in project options).",
            types.Schema(type=types.Type.OBJECT)
        )

    if tool_config["type"] == "simple":
        cmd = tool_config["command"]
        desc = (
            f"{tool_action}.\n"
            f"Configured command: '{cmd}'.\n"
            f"This command accepts no options or arguments. Call this tool with an empty object {{}}."
        )
        return (
            desc,
            types.Schema(type=types.Type.OBJECT, properties={})
        )

    if tool_config["type"] == "alternatives":
        alts = tool_config.get("alternatives", [])
        alt_lines = []
        cmd_enum = []
        for alt in alts:
            cmd = alt["command"]
            cmd_enum.append(cmd)
            alt_lines.append(f"- '{cmd}': {alt.get('description', f'Execute {cmd}')}")

        desc = (
            f"{tool_action}.\n"
            f"Select one of the following alternative commands to execute. "
            f"These alternative commands are all-or-nothing and do not accept any additional flags or arguments.\n"
            f"Available commands:\n" + "\n".join(alt_lines)
        )
        params = types.Schema(
            type=types.Type.OBJECT,
            properties={
                "command": types.Schema(
                    type=types.Type.STRING,
                    enum=cmd_enum,
                    description="The exact alternative command to run. Must be one of the allowed choices."
                )
            },
            required=["command"]
        )
        return desc, params

    if tool_config["type"] == "command_with_options":
        base_cmd = tool_config.get("command", "")
        options = tool_config.get("options", [])
        properties = {}
        required = []
        opt_lines = []

        for opt in options:
            name = opt["name"]
            opt_type = opt["type"]
            opt_desc = opt.get("description", "")
            choices = opt.get("choices")

            if opt_type == "option":
                flag = opt["flag"]
                has_value = opt["has_value"]
                if not has_value:
                    full_desc = f"Boolean flag '{flag}' (no value). Set to true to include this flag. {opt_desc}".strip()
                    properties[name] = types.Schema(
                        type=types.Type.BOOLEAN,
                        description=full_desc
                    )
                    opt_lines.append(f"- {name} (flag '{flag}', boolean): {opt_desc}")
                else:
                    if choices:
                        full_desc = f"Flag '{flag} <choice>'. Allowed values: {choices}. {opt_desc}".strip()
                        properties[name] = types.Schema(
                            type=types.Type.STRING,
                            enum=choices,
                            description=full_desc
                        )
                        opt_lines.append(f"- {name} (flag '{flag}', choices: {choices}): {opt_desc}")
                    else:
                        full_desc = f"Flag '{flag} <value>'. Free-form string argument. {opt_desc}".strip()
                        properties[name] = types.Schema(
                            type=types.Type.STRING,
                            description=full_desc
                        )
                        opt_lines.append(f"- {name} (flag '{flag}', string value): {opt_desc}")
            else:
                # Argument
                if choices:
                    full_desc = f"Positional argument (single string). Allowed values: {choices}. {opt_desc}".strip()
                    properties[name] = types.Schema(
                        type=types.Type.STRING,
                        enum=choices,
                        description=full_desc
                    )
                    opt_lines.append(f"- {name} (argument, choices: {choices}): {opt_desc}")
                else:
                    full_desc = f"Positional argument (single string). {opt_desc}".strip()
                    properties[name] = types.Schema(
                        type=types.Type.STRING,
                        description=full_desc
                    )
                    opt_lines.append(f"- {name} (argument, string): {opt_desc}")

            if opt.get("required"):
                required.append(name)

        summary_text = "\n".join(opt_lines) if opt_lines else "(No options configured)"
        desc = (
            f"{tool_action}.\n"
            f"Base command: '{base_cmd}'.\n"
            f"Available options and arguments:\n{summary_text}\n"
            f"Usage rules: You may pass any of the available options or arguments defined above within their valid schema. "
            f"Boolean flags should be true or false. Options with values and positional arguments must each be a single string argument."
        )
        params = types.Schema(
            type=types.Type.OBJECT,
            properties=properties,
            required=required if required else None
        )
        return desc, params

    return (f"{tool_action}.", types.Schema(type=types.Type.OBJECT))

def get_agent_tools(project_root=None):
    build_config = get_project_tool_config("build", start_dir=project_root)
    test_config = get_project_tool_config("test", start_dir=project_root)

    build_desc, build_schema = generate_tool_declaration_schema("build", build_config)
    test_desc, test_schema = generate_tool_declaration_schema("test", test_config)

    return [
        types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name='apply_patch',
                    description='Applies file modifications or creates new files. '
                        'You can provide the entire file contents using file_path and file_content '
                        '(strongly preferred, as a clean unified diff is generated locally to show the user), '
                        'or provide a unified diff patch via diff_content. '
                        'Ensure file paths are relative to the project root directory.',
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            'file_path': types.Schema(
                                type=types.Type.STRING,
                                description='The path to the file to modify or create, relative to the project root.'
                            ),
                            'file_content': types.Schema(
                                type=types.Type.STRING,
                                description='The complete, entire content of the file. A unified diff will be generated locally against the existing file.'
                            ),
                            'diff_content': types.Schema(
                                type=types.Type.STRING,
                                description='The unified diff patch to apply (optional if file_path and file_content are provided).'
                            )
                        }
                    )
                ),
                types.FunctionDeclaration(
                    name='read_file',
                    description='Reads the content of a file. Only files within the current working directory or its subdirectories can be read.',
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            'filepath': types.Schema(
                                type=types.Type.STRING,
                                description='Path to the file to read.'
                            )
                        },
                        required=['filepath']
                    )
                ),
                types.FunctionDeclaration(
                    name='list_directory',
                    description='Reads the list of files and directories in a given path. Cannot list above the current working directory.',
                    parameters=types.Schema(
                        type=types.Type.OBJECT,
                        properties={
                            'directory_path': types.Schema(
                                type=types.Type.STRING,
                                description='The relative path to the directory to list. Defaults to "." for the current directory.'
                            )
                        }
                    )
                ),
                types.FunctionDeclaration(
                    name='build_code',
                    description=build_desc,
                    parameters=build_schema
                ),
                types.FunctionDeclaration(
                    name='test_code',
                    description=test_desc,
                    parameters=test_schema
                )
            ]
        )
    ]

agent_tools = get_agent_tools()


def validate_patch_is_safe(temp_file_path, project_root=None):
    if not project_root:
        project_root = get_project_root()
    else:
        project_root = os.path.realpath(project_root)

    if not temp_file_path or not os.path.exists(temp_file_path):
        return False, f"Temp file does not exist: {temp_file_path}"

    try:
        with open(temp_file_path, 'r', encoding='utf-8', errors='replace') as f:
            diff_content = f.read()
    except Exception as e:
        return False, f"Error reading temp file: {e}"

    modified_files = set()
    for line in diff_content.split('\n'):
        if line.startswith('--- ') or line.startswith('+++ '):
            path_part = line[4:].split('\t')[0].strip()
            if path_part == '/dev/null':
                continue
            if path_part.startswith('a/') or path_part.startswith('b/'):
                path_part = path_part[2:]

            target_path = os.path.realpath(os.path.join(project_root, path_part))
            try:
                if os.path.commonpath([project_root, target_path]) != project_root:
                    return False, f"You are not permitted to modify files outside the project: {path_part}"
            except ValueError:
                return False, f"Path resolution failed for {path_part}. You are not permitted to modify files outside the project."

            modified_files.add(path_part)

    if not modified_files:
        return False, "No valid files found in patch to apply."

    return True, "Patch is safe."

class ChatSession(CommSession):
    def __init__(self, req_id, result_queue, agent_config=None, request=None):
        super().__init__(req_id, result_queue, agent_config=agent_config, request=request)
        self.method = "chat"
        self.client = None
        self.session = None
        self.project_root = None
        self.chat_history = None

    def execute_project_tool(self, tool, cmd, current_req_id, conn):
        tool_label = "build" if tool == "build_code" else "test"
        project_root = self.project_root or get_project_root()

        if not cmd:
            project_name = get_project_name(project_root)
            project_file_path = get_project_data_file_path(project_name, project_root) or "~/.var/vimini/projects/<project_name>"
            config_key = f"{tool_label}-command"
            msg = (
                f"\n[No {tool_label} command is defined for this project]\n"
                f"To configure a {tool_label} command, run :ViminiConfig or set '{config_key}' in the project file:\n"
                f"  {project_file_path}\n"
            )
            self.send_response(current_req_id, conn, result={
                "status": "chunk",
                "text": msg
            })
            return f"The tool '{tool}' is not available for this project because no {tool_label} command is configured in project options."

        self.send_response(current_req_id, conn, result={
            "status": "chunk",
            "text": f"\n[Executing {tool_label} command: {cmd}...]\n"
        })

        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=project_root,
                errors='replace'
            )

            output_lines = []
            for line in iter(proc.stdout.readline, ''):
                output_lines.append(line)
                self.send_response(current_req_id, conn, result={
                    "status": "chunk",
                    "text": line
                })

            proc.wait()
            returncode = proc.returncode
            full_output = "".join(output_lines).strip()
        except Exception as e:
            err_text = f"Execution error: {e}\n"
            self.send_response(current_req_id, conn, result={
                "status": "chunk",
                "text": err_text
            })
            returncode = -1
            full_output = err_text.strip()

        status_str = "succeeded" if returncode == 0 else f"failed with exit code {returncode}"
        chat_msg = f"\n[{tool_label.capitalize()} command {status_str}]\n"
        self.send_response(current_req_id, conn, result={
            "status": "chunk",
            "text": chat_msg
        })

        if returncode == 0:
            if full_output:
                return f"Command '{cmd}' executed successfully.\n\nCommand Output:\n{full_output}"
            else:
                return f"Command '{cmd}' executed successfully."
        else:
            if full_output:
                return f"{tool_label.capitalize()} command '{cmd}' failed (exit code {returncode}).\n\nCommand Output:\n{full_output}"
            else:
                return f"{tool_label.capitalize()} command '{cmd}' failed (exit code {returncode})."


    def _process_command(self, req_id, params, conn):
        if isinstance(params, dict) and params.get("terminate"):
            logger.info(f"Terminating ChatSession for req_id: {self.req_id}")
            self.running = False
            self.send_response(req_id, conn, result={"status": "terminated"})
            return

        prompt = params.get("prompt", "") if isinstance(params, dict) else ""
        if isinstance(params, dict) and params.get("project_root"):
            self.project_root = params.get("project_root")
        if not self.project_root:
            self.project_root = get_project_root()

        agent_config = self.agent_config or {}
        api_key = load_api_key(agent_config)
        model = agent_config.get("model")
        temperature = agent_config.get("temperature")
        if isinstance(params, dict) and params.get("temperature") is not None:
            temperature = params.get("temperature")
        verbose = False
        if isinstance(params, dict) and "verbose" in params:
            verbose = bool(params.get("verbose"))
        elif agent_config.get("verbose") is not None:
            verbose = bool(agent_config.get("verbose"))

        if prompt:
            logger.info(f"User prompt: {prompt}")

        if not self.session:
            self.client = get_client(config=agent_config)
            compilation_needed = get_project_config("compilation-needed", start_dir=self.project_root, default=False)
            build_config = get_project_tool_config("build", start_dir=self.project_root)
            test_config = get_project_tool_config("test", start_dir=self.project_root)

            tools_info = []
            if build_config:
                if build_config["type"] == "alternatives":
                    alts_str = ", ".join(repr(a["command"]) for a in build_config.get("alternatives", []))
                    tools_info.append(f"- `build_code`: Alternative build commands available: {alts_str}. Select one via `command` parameter (all-or-nothing, no extra options).")
                elif build_config["type"] == "command_with_options":
                    opts_str = ", ".join(f"{opt['name']} ({opt.get('flag', opt['name'])})" for opt in build_config.get("options", []))
                    tools_info.append(f"- `build_code`: Base command: '{build_config['command']}'. Available options/arguments: {opts_str or 'none'}.")
                else:
                    tools_info.append(f"- `build_code`: Simple command '{build_config['command']}'. Accepts no options.")
            elif not compilation_needed:
                tools_info.append("- `build_code`: Not configured and compilation is not needed.")

            if test_config:
                if test_config["type"] == "alternatives":
                    alts_str = ", ".join(repr(a["command"]) for a in test_config.get("alternatives", []))
                    tools_info.append(f"- `test_code`: Alternative test commands available: {alts_str}. Select one via `command` parameter (all-or-nothing, no extra options).")
                elif test_config["type"] == "command_with_options":
                    opts_str = ", ".join(f"{opt['name']} ({opt.get('flag', opt['name'])})" for opt in test_config.get("options", []))
                    tools_info.append(f"- `test_code`: Base command: '{test_config['command']}'. Available options/arguments: {opts_str or 'none'}.")
                else:
                    tools_info.append(f"- `test_code`: Simple command '{test_config['command']}'. Accepts no options.")
            else:
                tools_info.append("- `test_code`: No test command configured.")

            tools_summary = "\n".join(tools_info)

            if compilation_needed or build_config:
                build_test_guideline = (
                    "4. **Build and Test Tools:** Test and build tools may be expensive "
                    "and should be invoked only if the user instructions include a "
                    "request to build or test changes. You can use `build_code` to "
                    "compile/build the project and `test_code` to execute the project "
                    f"test suite to verify code changes or diagnose errors.\n"
                    "Running tests should be done only when necessary and if possible, "
                    "select only the specific test that needs to be verified instead of "
                    "running all the tests.\n"
                    f"Configured tools:\n{tools_summary}\n"
                    "When calling `build_code` or `test_code`, you MUST strictly adhere to the defined schema and allowed options. Unrecognized parameters or invalid values will cause the command to be rejected. "
                    "Do not attempt to work around command execution control by trying to add command execution in tests that is not actually testing the code."
                )
            else:
                build_test_guideline = (
                    "4. **Build and Test Tools:** Test and build tools may be expensive "
                    "and should be invoked only if the user instructions include a "
                    "request to build or test changes. This project does not require "
                    f"compilation and therefore the `build_code` tool should not be executed. "
                    f"You can use `test_code` to execute the project test suite to verify code changes or diagnose errors.\n"
                    "Running tests should be done only when necessary and if possible, "
                    "select only the specific test that needs to be verified instead of "
                    "running all the tests.\n"
                    f"Configured tools:\n{tools_summary}\n"
                    "When calling `test_code`, you MUST strictly adhere to the defined schema and allowed options. Unrecognized parameters or invalid values will cause the command to be rejected. "
                    "Do not attempt to work around command execution control by trying to add command execution in tests that is not actually testing the code."
                )
            current_tools = get_agent_tools(self.project_root)
            agent_config_obj = create_generation_config(
                tools=current_tools,
                temperature=temperature,
                verbose=verbose,
                system_instruction=(
                    "When explicitly requested to change code You act as an expert "
                    "autonomous coding agent and software engineer, and can access "
                    "tools and execute functions. "
                    "Normally although You are just an expert at returning general "
                    "information and avoid as much as possible using functions and "
                    "performing actions. "
                    "Your identity is Vimini, and you are integrated into the vimini project. "
                    "Follow these guidelines for optimal performance ONLY when "
                    "acting as a coding agent:\n"
                    "1. **Understand Context First:** Before proposing or applying any code changes, use `list_directory` and `read_file` tools to understand the repository structure and exact file contents. Never assume or guess code.\n"
                    "2. **Use the Patch Tool Correctly:** To modify or create files, use the `apply_patch` tool. You can provide the entire file contents using `file_path` and `file_content` (strongly preferred, as a unified diff will be generated locally to show the user) or provide a unified diff via `diff_content`. Use file paths relative to the project root.\n"
                    "3. **Patch Reliability:** `apply_patch` should ideally be the final action in your response. If a patch fails due to a formatting or context mismatch, do not blindly retry the exact same patch. Re-read the file to obtain up-to-date content and send the entire file contents using `file_path` and `file_content`.\n"
                    f"{build_test_guideline}\n"
                    "5. **Limit Retries:** Avoid multiple calls to `apply_patch` for the same file in a single response. If an apply_patch command is refused, do not retry and instead prompt the user for more instructions.\n"
                    "6. **Be Concise:** Provide brief, clear explanations. Avoid unnecessary conversational filler."
                ),
                disable_function_calling=False
            )

            kwargs = {
                "model": model,
                "config": agent_config_obj
            }
            if self.chat_history is not None:
                kwargs["history"] = self.chat_history
            self.session = self.client.chats.create(**kwargs)

        if not prompt:
            self.send_response(req_id, conn, result={"status": "ok", "text": ""})
            return

        def process_prompt_stream(current_prompt, current_req_id):
            pending_tool_calls = []
            try:
                response_stream = self.session.send_message_stream(current_prompt)
                for chunk in response_stream:
                    if chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts:
                        modified_text = ""
                        for part in chunk.candidates[0].content.parts:
                            if hasattr(part, 'function_call') and part.function_call:
                                tool_call = part.function_call
                                pending_tool_calls.append(tool_call)
                            elif getattr(part, 'thought', False):
                                thought_chunk = getattr(part, 'text', '') or ''
                                if thought_chunk:
                                    self.send_response(current_req_id, conn, result={
                                        "status": "thought",
                                        "thought": thought_chunk,
                                        "verbose": verbose
                                    })
                            elif hasattr(part, 'text') and part.text:
                                modified_text += part.text

                        if modified_text:
                            self.send_response(current_req_id, conn, result={"status": "chunk", "text": modified_text})
                    elif chunk.text:
                        self.send_response(current_req_id, conn, result={"status": "chunk", "text": chunk.text})
            except Exception as e:
                logger.error(f"Error in ChatSession for req_id {current_req_id}: {e}", exc_info=True)
                self.send_response(current_req_id, conn, result={
                    "status": "error",
                    "error": str(e)
                })
                if self.session:
                    try:
                        self.chat_history = self.session.get_history()
                    except Exception:
                        pass
                self.session = None
                return False

            if pending_tool_calls:
                responses = []
                next_req_id = current_req_id
                for tool_call in pending_tool_calls:
                    args_dict = dict(tool_call.args) if tool_call.args else {}
                    temp_file_path = None
                    logger.info(f"Chat[{req_id}]: tool use requested: {tool_call.name}")
                    if tool_call.name == 'apply_patch':
                        files_to_diff = []
                        if "files" in args_dict and isinstance(args_dict["files"], list):
                            for f_item in args_dict["files"]:
                                if isinstance(f_item, dict):
                                    f_path = f_item.get("file_path") or f_item.get("filepath")
                                    f_content = f_item.get("file_content") if "file_content" in f_item else f_item.get("content")
                                    if f_path is not None and f_content is not None:
                                        files_to_diff.append((f_path, f_content))

                        file_path = args_dict.get('file_path') or args_dict.get('filepath')
                        file_content = args_dict.get('file_content') if 'file_content' in args_dict else args_dict.get('content')
                        diff_content = args_dict.get('diff_content', '')

                        if not files_to_diff and file_path and file_content is not None:
                            files_to_diff.append((file_path, file_content))

                        # If full content was mistakenly provided in diff_content along with file_path
                        if not files_to_diff and file_path and diff_content:
                            stripped_diff = diff_content.lstrip()
                            if not (stripped_diff.startswith("diff --git") or stripped_diff.startswith("--- ") or stripped_diff.startswith("@@ ")) and "\n@@ " not in diff_content:
                                files_to_diff.append((file_path, diff_content))
                                diff_content = ""

                        if files_to_diff:
                            diff_parts = []
                            diff_errors = []
                            for f_path, f_content in files_to_diff:
                                f_diff, err = generate_diff_for_file(f_path, f_content, self.project_root)
                                if err:
                                    diff_errors.append(err)
                                elif f_diff:
                                    diff_parts.append(f_diff)

                            if diff_errors:
                                responses.append(types.Part.from_function_response(
                                    name=tool_call.name,
                                    response={'result': f"Patch generation failed:\n" + "\n".join(diff_errors)}
                                ))
                                continue

                            if not diff_parts:
                                responses.append(types.Part.from_function_response(
                                    name=tool_call.name,
                                    response={'result': "The provided file content is identical to the existing file; no modifications were detected."}
                                ))
                                continue

                            diff_content = "".join(diff_parts)

                        if not diff_content:
                            responses.append(types.Part.from_function_response(
                                name=tool_call.name,
                                response={'result': "Patch failed: Neither valid 'file_content' (with 'file_path') nor 'diff_content' was provided."}
                            ))
                            continue

                        with tempfile.NamedTemporaryFile(mode='w', suffix='.diff', delete=False, encoding='utf-8') as f:
                            f.write(diff_content)
                            temp_file_path = f.name

                        is_safe, err_msg = validate_patch_is_safe(temp_file_path, self.project_root)
                        if not is_safe:
                            if temp_file_path and os.path.exists(temp_file_path):
                                try:
                                    os.remove(temp_file_path)
                                except Exception:
                                    pass
                            responses.append(types.Part.from_function_response(
                                name=tool_call.name,
                                response={'result': f"Patch validation failed: {err_msg}"}
                            ))
                            continue

                        target_desc = ", ".join(f[0] for f in files_to_diff) if files_to_diff else (file_path or "")
                        desc_str = f" for {target_desc}" if target_desc else ""
                        req_msg = f"\n[Agent requested tool execution: apply_patch{desc_str}. Patch saved to temp file: {temp_file_path}]\n"
                        self.send_response(current_req_id, conn, result={
                            "status": "tool_use_requested",
                            "tool": tool_call.name,
                            "file_path": target_desc,
                            "temp_file": temp_file_path,
                            "text": req_msg
                        })
                    elif tool_call.name in ('build_code', 'test_code'):
                        tool_label = "build" if tool_call.name == "build_code" else "test"
                        tool_config = get_project_tool_config(tool_label, start_dir=self.project_root)
                        if not tool_config:
                            project_name = get_project_name(self.project_root)
                            project_file_path = get_project_data_file_path(project_name, self.project_root) or "~/.var/vimini/projects/<project_name>"
                            config_key = f"{tool_label}-command"
                            msg = (
                                f"\n[No {tool_label} command is defined for this project]\n"
                                f"To configure a {tool_label} command, run :ViminiConfig or set '{config_key}' in the project file:\n"
                                f"  {project_file_path}\n"
                            )
                            self.send_response(current_req_id, conn, result={
                                "status": "chunk",
                                "text": msg
                            })
                            responses.append(types.Part.from_function_response(
                                name=tool_call.name,
                                response={'result': f"The tool '{tool_call.name}' is not available for this project because no {tool_label} command is configured in project options."}
                            ))
                            continue

                        is_valid, res_or_err = validate_tool_call(tool_label, args_dict, tool_config)
                        if not is_valid:
                            logger.warning(f"Tool {tool_call.name} rejected schema check: {res_or_err}")
                            self.send_response(current_req_id, conn, result={
                                "status": "chunk",
                                "text": f"\n[Agent {tool_label} command rejected due to schema error:\n{res_or_err}]\n"
                            })
                            responses.append(types.Part.from_function_response(
                                name=tool_call.name,
                                response={'result': f"Command rejected: {res_or_err}"}
                            ))
                            continue

                        composed_cmd = res_or_err
                        cmd_info = f": {composed_cmd}"
                        req_msg = f"\n[Agent requested tool execution: {tool_call.name}{cmd_info}]\n"
                        self.send_response(current_req_id, conn, result={
                            "status": "tool_use_requested",
                            "tool": tool_call.name,
                            "command": composed_cmd,
                            "args": args_dict,
                            "text": req_msg
                        })
                    else:
                        args_str = json.dumps(args_dict)
                        req_msg = f"\n[Agent requested tool execution: {tool_call.name}({args_str})]\n"
                        self.send_response(current_req_id, conn, result={
                            "status": "tool_use_requested",
                            "tool": tool_call.name,
                            "args": args_dict,
                            "text": req_msg
                        })

                    try:
                        next_item = self.cmd_queue.get()
                        if isinstance(next_item, tuple) and len(next_item) == 3:
                            next_req_id, next_params, next_conn = next_item
                        else:
                            next_params, next_conn = next_item
                            next_req_id = current_req_id
                    except Exception as e:
                        logger.error(f"Error waiting for client response: {e}")
                        next_req_id, next_params, next_conn = current_req_id, {}, conn

                    logger.info(f"Received tool response from user: {next_params}")

                    if isinstance(next_params, dict) and next_params.get("terminate"):
                        logger.info(f"Terminating ChatSession for req_id: {self.req_id}")
                        self.running = False
                        self.send_response(current_req_id, conn, result={"status": "terminated"})
                        return False

                    is_approved = False
                    if isinstance(next_params, dict):
                        if "approved" in next_params:
                            is_approved = bool(next_params["approved"])
                        elif "approval" in next_params:
                            is_approved = bool(next_params["approval"])
                        elif "prompt" in next_params and isinstance(next_params["prompt"], str):
                            p = next_params["prompt"].strip().lower()
                            if p in ("yes", "y", "approve", "approved", "ok"):
                                is_approved = True

                    if tool_call.name == 'list_directory':
                        if is_approved:
                            dir_path = args_dict.get('directory_path', '.')
                            result_text = list_directory(dir_path, self.project_root)
                        else:
                            result_text = "Tool execution cancelled or rejected by user."
                        responses.append(types.Part.from_function_response(
                            name=tool_call.name,
                            response={'result': result_text}
                        ))
                    elif tool_call.name == 'read_file':
                        if is_approved:
                            filepath = args_dict.get('filepath', '')
                            result_text = read_file(filepath, self.project_root)
                        else:
                            result_text = "Tool execution cancelled or rejected by user."
                        responses.append(types.Part.from_function_response(
                            name=tool_call.name,
                            response={'result': result_text}
                        ))
                    elif tool_call.name == 'apply_patch':
                        if temp_file_path and os.path.exists(temp_file_path):
                            try:
                                os.remove(temp_file_path)
                            except Exception:
                                pass

                        error_msg = next_params.get("error") if isinstance(next_params, dict) else None
                        if error_msg:
                            if "retry" not in error_msg.lower():
                                patch_result = f"Patch failed to apply:\n{error_msg}\nPlease send the entire file contents using file_path and file_content, or verify that the patch is properly formatted and retry."
                            else:
                                patch_result = error_msg
                        elif is_approved:
                            patch_result = True
                        else:
                            patch_result = "Apply patch command was refused. Do not retry and instead prompt the user for more instructions."

                        responses.append(types.Part.from_function_response(
                            name=tool_call.name,
                            response={'result': patch_result}
                        ))
                    elif tool_call.name in ('build_code', 'test_code'):
                        tool_label = "build" if tool_call.name == "build_code" else "test"
                        if is_approved:
                            result_text = self.execute_project_tool(tool_call.name, composed_cmd, current_req_id, conn)
                        else:
                            self.send_response(current_req_id, conn, result={
                                "status": "chunk",
                                "text": f"\n[{tool_label.capitalize()} command execution rejected by user]\n"
                            })
                            result_text = "Tool execution cancelled or rejected by user."
                        responses.append(types.Part.from_function_response(
                            name=tool_call.name,
                            response={'result': result_text}
                        ))
                    else:
                        if is_approved:
                            result_text = "Tool executed."
                        else:
                            result_text = "Tool execution rejected by user."
                        responses.append(types.Part.from_function_response(
                            name=tool_call.name,
                            response={'result': result_text}
                        ))

                return process_prompt_stream(responses, next_req_id)

            return True

        if not process_prompt_stream(prompt, req_id):
            return
        try:
            self.chat_history = self.session.get_history()
        except Exception:
            pass
        if not self.running:
            return
        self.send_response(req_id, conn, result={"status": "done"})
