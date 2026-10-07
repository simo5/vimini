import os
import logging
import re
import json
import subprocess
from google.genai import types
from vimini.agent.comms import CommSession
from vimini.common.genai import get_client, create_generation_config
from vimini.common.util import (
    list_directory,
    read_file,
    get_project_root,
    temporary_git_worktree,
)

logger = logging.getLogger("vimini_agent")

review_tools = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="read_file",
                description="Reads the content of a file. Only files within the current working directory or its subdirectories can be read. Use sparingly and only as needed.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "filepath": types.Schema(
                            type=types.Type.STRING,
                            description="Path to the file to read.",
                        )
                    },
                    required=["filepath"],
                ),
            ),
            types.FunctionDeclaration(
                name="list_directory",
                description="Reads the list of files and directories in a given path. Cannot list above the current working directory. Use sparingly and only as needed.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "directory_path": types.Schema(
                            type=types.Type.STRING,
                            description='The relative path to the directory to list. Defaults to "." for the current directory.',
                        )
                    },
                ),
            ),
        ]
    )
]


def _construct_review_prompt(
    prompt, review_content, content_source_description, security_focus
):
    """
    Constructs the prompt for the review.
    """
    if security_focus:
        review_instructions = (
            f"Please review {content_source_description} exclusively for potential security issues or hazards. "
            "Focus on identifying vulnerabilities, insecure coding practices, and potential attack vectors. "
            "Provide clear, actionable suggestions for mitigation. Do not comment on code style, "
            "performance, or other non-security aspects."
        )
    else:
        review_instructions = (
            f"Please review {content_source_description} for potential issues, "
            "improvements, best practices, and any possible bugs. "
            "Provide a concise summary and actionable suggestions."
        )

    tools_guideline = (
        "You have access to `read_file` and `list_directory` tools to inspect project files "
        "and directory structure if you need additional context to perform an accurate review. "
        "Directory listing and file reading should be used sparingly and only as needed. "
        "There is a maximum limit of 15 tool iterations, so inspect only essential files and conclude your review promptly."
    )

    prompt_text = (
        f"{review_instructions}\n\n"
        f"{tools_guideline}\n\n"
        "--- CONTENT TO REVIEW ---\n"
        f"{review_content}\n"
        "--- END CONTENT TO REVIEW ---"
        f"\n{prompt}\n"
    )
    return prompt_text


def _execute_review_stream(
    chat_session,
    initial_prompt,
    project_root,
    on_chunk=None,
    on_thought=None,
    on_tool_call=None,
    on_tool_result=None,
    check_running=None,
    max_turns=15,
):
    """
    Executes a review stream loop, handling tool calls (read_file, list_directory)
    automatically until completion or reaching max_turns limit.
    """
    current_input = initial_prompt
    turn = 0
    while True:
        if check_running and not check_running():
            break

        response_stream = chat_session.send_message_stream(current_input)
        pending_tool_calls = []

        for chunk in response_stream:
            if check_running and not check_running():
                break

            if hasattr(chunk, "candidates") and chunk.candidates:
                candidate = chunk.candidates[0]
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if hasattr(part, "function_call") and part.function_call:
                            pending_tool_calls.append(part.function_call)
                        elif getattr(part, "thought", False):
                            thought_chunk = getattr(part, "text", "") or ""
                            if thought_chunk and on_thought:
                                on_thought(thought_chunk)
                        elif hasattr(part, "text") and part.text:
                            if on_chunk:
                                on_chunk(part.text)
            elif hasattr(chunk, "text"):
                try:
                    if chunk.text and on_chunk:
                        on_chunk(chunk.text)
                except Exception:
                    pass

        if not pending_tool_calls or (check_running and not check_running()):
            break

        turn += 1
        if max_turns is not None and turn > max_turns:
            logger.warning(
                f"Review reached maximum tool iteration limit ({max_turns})."
            )
            break

        responses = []
        for tool_call in pending_tool_calls:
            args_dict = dict(tool_call.args) if tool_call.args else {}
            logger.info(f"Review tool call: {tool_call.name}({args_dict})")
            if on_tool_call:
                on_tool_call(tool_call.name, args_dict)
            elif on_chunk:
                args_str = json.dumps(args_dict) if args_dict else ""
                on_chunk(
                    f"\n[Agent requested tool execution: {tool_call.name}({args_str})]\n"
                )

            try:
                if tool_call.name == "list_directory":
                    dir_path = args_dict.get("directory_path", ".")
                    raw_result = list_directory(dir_path, project_root=project_root)
                elif tool_call.name == "read_file":
                    filepath = args_dict.get("filepath", "")
                    raw_result = read_file(filepath, project_root=project_root)
                else:
                    raw_result = f"Unknown tool: {tool_call.name}"
            except Exception as e:
                raw_result = f"Error executing {tool_call.name}: {e}"

            is_error = raw_result.startswith(
                ("Error", "Security error", "Unknown tool")
            )
            if is_error:
                logger.warning(
                    f"Review tool call failed: {tool_call.name}({args_dict}) -> {raw_result}"
                )

            if on_tool_result:
                on_tool_result(tool_call.name, args_dict, raw_result, is_error)

            if max_turns is not None and turn == max_turns:
                note = f"\n\n[Note: Maximum tool iteration limit reached ({max_turns}). Please conclude your review now without requesting further tool calls.]"
                result_text = raw_result + note
            else:
                result_text = raw_result

            responses.append(
                types.Part.from_function_response(
                    name=tool_call.name, response={"result": result_text}
                )
            )

        current_input = responses


class ReviewSession(CommSession):
    def __init__(self, req_id, result_queue, agent_config=None, request=None):
        super().__init__(
            req_id, result_queue, agent_config=agent_config, request=request
        )
        self.method = "review"

    def _check_running(self, current_req_id, conn):
        unhandled_items = []
        while not self.cmd_queue.empty():
            try:
                cmd_item = self.cmd_queue.get_nowait()
                if isinstance(cmd_item, tuple) and len(cmd_item) == 3:
                    c_req_id, c_params, c_conn = cmd_item
                else:
                    c_params, c_conn = cmd_item
                    c_req_id = current_req_id
                if isinstance(c_params, dict) and c_params.get("terminate"):
                    logger.info(f"Terminating ReviewSession for req_id: {self.req_id}")
                    self.running = False
                    self.send_response(
                        c_req_id, c_conn, result={"status": "terminated"}
                    )
                    break
                else:
                    unhandled_items.append(cmd_item)
            except Exception:
                break

        for item in unhandled_items:
            self.cmd_queue.put(item)

        return self.running

    def _process_command(self, req_id, params, conn):
        params = params if isinstance(params, dict) else {}
        if params.get("terminate"):
            logger.info(f"Terminating ReviewSession for req_id: {self.req_id}")
            self.running = False
            self.send_response(req_id, conn, result={"status": "terminated"})
            return

        if params.get("batch"):
            self._handle_batch_review(req_id, params, conn)
            return

        self._handle_interactive_review(req_id, params, conn)

    def _handle_batch_review(self, req_id, params, conn):
        agent_config = self.agent_config or {}
        model = agent_config.get("model")

        prompt = params.get("prompt", "")
        security_focus = params.get("security_focus", False)
        verbose = params.get("verbose", False)

        repo_path = params.get("project_root")
        if not repo_path:
            repo_path = get_project_root()
        commit_list = params.get("commit_list", [])
        target_dir = params.get("target_dir", repo_path)
        worktree_ref = params.get("worktree_ref")

        try:
            client = get_client(config=agent_config)
            total_commits = len(commit_list)

            with temporary_git_worktree(repo_path, worktree_ref) as active_root:
                for index, commit_sha in enumerate(commit_list):
                    if not self._check_running(req_id, conn):
                        break
                    patch_num = index + 1
                    status_msg = f"Reviewing commit {patch_num}/{total_commits}: {commit_sha[:7]}... (Async)"
                    self.send_response(
                        req_id,
                        conn,
                        result={"status": "progress", "message": status_msg},
                    )

                    if active_root != repo_path:
                        subprocess.run(
                            [
                                "git",
                                "-C",
                                active_root,
                                "checkout",
                                "--detach",
                                commit_sha,
                            ],
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            check=False,
                        )

                    cmd_show = ["git", "-C", repo_path, "show", commit_sha]
                    result_show = subprocess.run(
                        cmd_show,
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        check=False,
                    )
                    if result_show.returncode != 0:
                        err = (result_show.stderr or "git show failed.").strip()
                        self.send_response(
                            req_id,
                            conn,
                            result={
                                "status": "progress",
                                "message": f"Skipping {commit_sha[:7]}: {err}",
                                "error": True,
                            },
                        )
                        continue
                    review_content_single = result_show.stdout

                    prompt_text = _construct_review_prompt(
                        prompt,
                        review_content_single,
                        f"the output of `git show {commit_sha[:7]}`",
                        security_focus,
                    )

                    generation_config = create_generation_config(
                        tools=review_tools,
                        verbose=verbose,
                        disable_function_calling=False,
                    )

                    current_review_accumulator = []
                    try:
                        chat = client.chats.create(
                            model=model, config=generation_config
                        )

                        def on_batch_tool_call(tool_name, tool_args):
                            args_str = json.dumps(tool_args) if tool_args else ""
                            self.send_response(
                                req_id,
                                conn,
                                result={
                                    "status": "progress",
                                    "message": f"Commit {commit_sha[:7]} tool call: {tool_name}({args_str})",
                                },
                            )

                        def on_batch_tool_result(
                            tool_name, tool_args, result_text, is_error
                        ):
                            if is_error:
                                err_line = (
                                    result_text.strip().splitlines()[0]
                                    if result_text
                                    else "Tool execution failed"
                                )
                                self.send_response(
                                    req_id,
                                    conn,
                                    result={
                                        "status": "progress",
                                        "message": f"Commit {commit_sha[:7]} tool call failed: {err_line}",
                                        "error": True,
                                    },
                                )

                        _execute_review_stream(
                            chat,
                            prompt_text,
                            active_root,
                            on_chunk=lambda text: current_review_accumulator.append(
                                text
                            ),
                            on_tool_call=on_batch_tool_call,
                            on_tool_result=on_batch_tool_result,
                            check_running=lambda: self._check_running(req_id, conn),
                        )

                        if not self.running:
                            break

                        subject_cmd = [
                            "git",
                            "-C",
                            repo_path,
                            "log",
                            "-1",
                            "--pretty=%s",
                            commit_sha,
                        ]
                        subject_result = subprocess.run(
                            subject_cmd,
                            capture_output=True,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            check=False,
                        )
                        subject = (
                            subject_result.stdout.strip()
                            if subject_result.returncode == 0
                            else "commit"
                        )

                        sanitized_subject = (
                            re.sub(r"[^a-zA-Z0-9]+", "-", subject).strip("-").lower()
                        )
                        sanitized_subject = sanitized_subject[:50]

                        filename = f"{patch_num:04d}-{sanitized_subject}.review.txt"
                        filepath = os.path.join(target_dir, filename)

                        content = "".join(current_review_accumulator)
                        with open(filepath, "w", encoding="utf-8") as f:
                            f.write(content)

                        self.send_response(
                            req_id,
                            conn,
                            result={
                                "status": "progress",
                                "message": f"Saved review to {filename}",
                            },
                        )

                    except Exception as e:
                        logger.error(
                            f"Error reviewing commit {commit_sha[:7]}: {e}",
                            exc_info=True,
                        )
                        self.send_response(
                            req_id,
                            conn,
                            result={
                                "status": "progress",
                                "message": f"Error reviewing {commit_sha[:7]}: {e}",
                                "error": True,
                            },
                        )

            if not self.running:
                return

            self.send_response(
                req_id,
                conn,
                result={
                    "status": "batch_completed",
                    "message": "All reviews completed and saved.",
                },
            )
        except Exception as e:
            logger.error(
                f"Error in batch ReviewSession for req_id {req_id}: {e}", exc_info=True
            )
            self.send_response(
                req_id, conn, result={"status": "error", "error": str(e)}
            )
        finally:
            self.running = False

    def _handle_interactive_review(self, req_id, params, conn):
        agent_config = self.agent_config or {}
        model = agent_config.get("model")

        prompt = params.get("prompt", "")
        review_content = params.get("review_content", "")
        content_source_description = params.get("content_source_description", "")
        security_focus = params.get("security_focus", False)
        verbose = params.get("verbose", False)

        project_root = params.get("project_root")
        if not project_root:
            project_root = get_project_root()
        worktree_ref = params.get("worktree_ref")

        try:
            client = get_client(config=agent_config)

            prompt_text = _construct_review_prompt(
                prompt, review_content, content_source_description, security_focus
            )

            generation_config = create_generation_config(
                tools=review_tools,
                verbose=verbose,
                disable_function_calling=False,
            )

            chat = client.chats.create(model=model, config=generation_config)

            def on_chunk(text):
                self.send_response(
                    req_id, conn, result={"status": "chunk", "text": text}
                )

            def on_thought(thought_text):
                self.send_response(
                    req_id, conn, result={"status": "thought", "thought": thought_text}
                )

            def on_tool_call(tool_name, tool_args):
                args_str = json.dumps(tool_args) if tool_args else ""
                self.send_response(
                    req_id,
                    conn,
                    result={
                        "status": "tool_use_requested",
                        "tool": tool_name,
                        "args": tool_args,
                        "text": f"\n[Agent requested tool execution: {tool_name}({args_str})]\n",
                    },
                )

            with temporary_git_worktree(project_root, worktree_ref) as active_root:
                _execute_review_stream(
                    chat,
                    prompt_text,
                    active_root,
                    on_chunk=on_chunk,
                    on_thought=on_thought,
                    on_tool_call=on_tool_call,
                    check_running=lambda: self._check_running(req_id, conn),
                )

            if not self.running:
                return

            self.send_response(req_id, conn, result={"status": "completed"})
        except Exception as e:
            logger.error(
                f"Error in ReviewSession for req_id {req_id}: {e}", exc_info=True
            )
            self.send_response(
                req_id, conn, result={"status": "error", "error": str(e)}
            )
        finally:
            self.running = False
