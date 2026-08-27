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
    list_directory,
    read_file
)
from vimini.agent.comms import CommSession
from vimini.common.genai import get_client, load_api_key, create_generation_config

logger = logging.getLogger('vimini_agent')

agent_tools = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name='apply_patch',
                description='Applies a unified diff patch to modify files. '
                    'Ensure the patch paths are relative to the project root '
                    'directory. Assume patch -p1 will be used. '
                    'Include sufficient unmodified context lines for the patch to apply cleanly.',
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        'diff_content': types.Schema(
                            type=types.Type.STRING,
                            description='The unified diff patch to apply.'
                        )
                    },
                    required=['diff_content']
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
                description='Compiles or builds the code in the project.',
                parameters=types.Schema(
                    type=types.Type.OBJECT
                )
            ),
            types.FunctionDeclaration(
                name='test_code',
                description='Runs the project test suite.',
                parameters=types.Schema(
                    type=types.Type.OBJECT
                )
            )
        ]
    )
]


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

    def execute_project_tool(self, tool, current_req_id, conn):
        tool_label = "build" if tool == "build_code" else "test"
        project_root = self.project_root or get_project_root()
        cmd = get_project_config(f"{tool_label}-command", start_dir=project_root)

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
            if compilation_needed:
                build_test_guideline = (
                    "4. **Build and Test Tools:** Test and build tools may be expensive "
                    "and should be invoked only if the user instructions include a "
                    "request to build or test changes. You can use `build_code` to "
                    "compile/build the project and `test_code` to execute the project "
                    "test suite to verify code changes or diagnose errors."
                )
            else:
                build_test_guideline = (
                    "4. **Build and Test Tools:** Test and build tools may be expensive "
                    "and should be invoked only if the user instructions include a "
                    "request to build or test changes. This project does not require "
                    "compilation and therefore the `build_code` tool should not be executed. "
                    "You can use `test_code` to execute the project test suite to verify "
                    "code changes or diagnose errors."
                )
            agent_config_obj = create_generation_config(
                tools=agent_tools,
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
                    "2. **Use the Patch Tool Correctly:** To modify files, use the `apply_patch` tool. Provide a valid unified diff. Use file paths relative to the project root. Ensure your diff includes sufficient unmodified context lines for reliable application.\n"
                    "3. **Patch Reliability:** `apply_patch` should ideally be the final action in your response. If a patch fails due to a formatting or context mismatch, do not blindly retry the exact same patch. First, re-read the file to obtain the up-to-date content, then formulate a corrected diff.\n"
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
                        diff_content = args_dict.get('diff_content', '')
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

                        req_msg = f"\n[Agent requested tool execution: apply_patch. Patch saved to temp file: {temp_file_path}]\n"
                        self.send_response(current_req_id, conn, result={
                            "status": "tool_use_requested",
                            "tool": tool_call.name,
                            "temp_file": temp_file_path,
                            "text": req_msg
                        })
                    elif tool_call.name in ('build_code', 'test_code'):
                        tool_label = "build" if tool_call.name == "build_code" else "test"
                        cmd = get_project_config(f"{tool_label}-command", start_dir=self.project_root)
                        if not cmd:
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
                        cmd_info = f": {cmd}" if cmd else ""
                        req_msg = f"\n[Agent requested tool execution: {tool_call.name}{cmd_info}]\n"
                        self.send_response(current_req_id, conn, result={
                            "status": "tool_use_requested",
                            "tool": tool_call.name,
                            "command": cmd,
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
                            if "verify that the patch is properly formatted and retry" not in error_msg.lower():
                                patch_result = f"Patch failed to apply:\n{error_msg}\nPlease verify that the patch is properly formatted and retry."
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
                            result_text = self.execute_project_tool(tool_call.name, current_req_id, conn)
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
