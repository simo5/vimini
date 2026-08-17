import os
import json
import queue
import subprocess
import logging
import tempfile
import sys
from google import genai
from google.genai import types
from vimini.common.util import get_project_root
from vimini.agent.comms import CommSession
from vimini.agent.server import load_api_key

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
            )
        ]
    )
]

def list_directory(directory_path="."):
    try:
        project_root = get_project_root()
        target_path = os.path.abspath(os.path.join(project_root, directory_path))

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

def read_file(filepath):
    try:
        project_root = get_project_root()
        target_path = os.path.abspath(os.path.join(project_root, filepath))

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

def validate_patch_is_safe(temp_file_path):
    project_root = get_project_root()

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

            target_path = os.path.abspath(os.path.join(project_root, path_part))
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

    def _process_command(self, req_id, params, conn):
        if isinstance(params, dict) and params.get("terminate"):
            logger.info(f"Terminating ChatSession for req_id: {self.req_id}")
            self.running = False
            self.send_response(req_id, conn, result={"status": "terminated"})
            return

        prompt = params.get("prompt", "") if isinstance(params, dict) else ""
        agent_config = self.agent_config or {}
        api_key = load_api_key(agent_config)
        model = agent_config.get("model")

        if prompt:
            logger.info(f"User prompt: {prompt}")

        if not self.session:
            self.client = genai.Client(api_key=api_key) if api_key else genai.Client()
            agent_config_obj = types.GenerateContentConfig(
                tools=agent_tools,
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
                    "4. **Limit Retries:** Avoid multiple calls to `apply_patch` for the same file in a single response. If an apply_patch command is refused, do not retry and instead prompt the user for more instructions.\n"
                    "5. **Be Concise:** Provide brief, clear explanations. Avoid unnecessary conversational filler."
                )
            )
            self.session = self.client.chats.create(
                model=model,
                config=agent_config_obj
            )

        if not prompt:
            self.send_response(req_id, conn, result={"status": "ok", "text": ""})
            return

        def process_prompt_stream(current_prompt, current_req_id):
            response_stream = self.session.send_message_stream(current_prompt)
            pending_tool_calls = []
            for chunk in response_stream:
                if chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts:
                    modified_text = ""
                    for part in chunk.candidates[0].content.parts:
                        if hasattr(part, 'function_call') and part.function_call:
                            tool_call = part.function_call
                            pending_tool_calls.append(tool_call)
                        elif hasattr(part, 'text') and part.text:
                            modified_text += part.text

                    if modified_text:
                        self.send_response(current_req_id, conn, result={"status": "chunk", "text": modified_text})
                elif chunk.text:
                    self.send_response(current_req_id, conn, result={"status": "chunk", "text": chunk.text})

            if pending_tool_calls:
                responses = []
                for tool_call in pending_tool_calls:
                    args_dict = dict(tool_call.args) if tool_call.args else {}
                    temp_file_path = None
                    logger.info(f"Chat[{req_id}]: tool use requested: {tool_call.name}")
                    if tool_call.name == 'apply_patch':
                        diff_content = args_dict.get('diff_content', '')
                        with tempfile.NamedTemporaryFile(mode='w', suffix='.diff', delete=False, encoding='utf-8') as f:
                            f.write(diff_content)
                            temp_file_path = f.name

                        is_safe, err_msg = validate_patch_is_safe(temp_file_path)
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
                        return

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
                            result_text = list_directory(dir_path)
                        else:
                            result_text = "Tool execution cancelled or rejected by user."
                        responses.append(types.Part.from_function_response(
                            name=tool_call.name,
                            response={'result': result_text}
                        ))
                    elif tool_call.name == 'read_file':
                        if is_approved:
                            filepath = args_dict.get('filepath', '')
                            result_text = read_file(filepath)
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

                        if is_approved:
                            patch_result = True
                        else:
                            patch_result = "Apply patch command was refused. Do not retry and instead prompt the user for more instructions."

                        responses.append(types.Part.from_function_response(
                            name=tool_call.name,
                            response={'result': patch_result}
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

                process_prompt_stream(responses, next_req_id)

        process_prompt_stream(prompt, req_id)
        self.send_response(req_id, conn, result={"status": "done"})
