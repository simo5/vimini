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
    list_git_commits,
    get_git_commit,
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
            types.FunctionDeclaration(
                name="list_git_commits",
                description="Lists recent git commits in the repository. Use this to inspect history, identify commit IDs, or track changes to specific files.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "max_count": types.Schema(
                            type=types.Type.INTEGER,
                            description="Maximum number of commits to list (default 10, max 50).",
                        ),
                        "revision": types.Schema(
                            type=types.Type.STRING,
                            description="Git revision, branch, or range to list commits from (e.g. 'HEAD', 'main', 'HEAD~5..HEAD'). Defaults to 'HEAD'.",
                        ),
                        "file_path": types.Schema(
                            type=types.Type.STRING,
                            description="Optional relative file or directory path to filter commits affecting that path.",
                        ),
                    },
                ),
            ),
            types.FunctionDeclaration(
                name="get_git_commit",
                description="Retrieves the commit message and diff/patch for a specific git commit ID. Use this to inspect what changed in a previous commit.",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "commit_id": types.Schema(
                            type=types.Type.STRING,
                            description="The commit hash, short hash, or ref to inspect (e.g., 'a1b2c3d', 'HEAD~1').",
                        ),
                        "file_path": types.Schema(
                            type=types.Type.STRING,
                            description="Optional relative file path to view diff for that specific file only.",
                        ),
                        "stat_only": types.Schema(
                            type=types.Type.BOOLEAN,
                            description="If true, returns only the diffstat instead of the full patch diff. Useful for large commits.",
                        ),
                    },
                    required=["commit_id"],
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
        "You have access to `read_file`, `list_directory`, `list_git_commits`, and `get_git_commit` tools to inspect project files, "
        "directory structure, and git commit history if you need additional context to perform an accurate review. "
        "Directory listing, file reading, and commit inspections should be used sparingly and only as needed. "
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
    session,
    req_id,
    conn,
    chat_session,
    initial_prompt,
    project_root,
    verbose=False,
    save=False,
    commit_sha=None,
    max_turns=15,
):
    """
    Executes a review stream loop, handling tool calls (read_file, list_directory)
    automatically until completion or reaching max_turns limit.
    Output batching is done only when save is True, otherwise thoughts and text chunks
    are streamed directly to the client.
    """
    accumulator = []
    current_input = initial_prompt
    turn = 0
    while True:
        if not session._check_running(req_id, conn):
            break

        response_stream = chat_session.send_message_stream(current_input)
        pending_tool_calls = []

        for chunk in response_stream:
            if not session._check_running(req_id, conn):
                break

            if (
                hasattr(chunk, "candidates")
                and chunk.candidates
                and chunk.candidates[0].content
                and chunk.candidates[0].content.parts
            ):
                candidate = chunk.candidates[0]
                modified_text = ""
                for part in candidate.content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        pending_tool_calls.append(part.function_call)
                    elif getattr(part, "thought", False):
                        thought_chunk = getattr(part, "text", "") or ""
                        if thought_chunk and not save:
                            session.send_response(
                                req_id,
                                conn,
                                result={
                                    "status": "thought",
                                    "thought": thought_chunk,
                                    "verbose": verbose,
                                },
                            )
                    elif hasattr(part, "text") and part.text:
                        modified_text += part.text

                if modified_text:
                    if save:
                        accumulator.append(modified_text)
                    else:
                        session.send_response(
                            req_id,
                            conn,
                            result={"status": "chunk", "text": modified_text},
                        )
            elif hasattr(chunk, "text"):
                try:
                    if chunk.text:
                        if save:
                            accumulator.append(chunk.text)
                        else:
                            session.send_response(
                                req_id,
                                conn,
                                result={"status": "chunk", "text": chunk.text},
                            )
                except Exception:
                    pass

        if not pending_tool_calls or not session._check_running(req_id, conn):
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
            args_str = json.dumps(args_dict) if args_dict else ""

            if save:
                commit_prefix = f"Commit {commit_sha[:7]} " if commit_sha else ""
                session.send_response(
                    req_id,
                    conn,
                    result={
                        "status": "progress",
                        "message": f"{commit_prefix}tool call: {tool_call.name}({args_str})",
                    },
                )
            else:
                session.send_response(
                    req_id,
                    conn,
                    result={
                        "status": "tool_use_requested",
                        "tool": tool_call.name,
                        "args": args_dict,
                        "text": f"\n[Agent requested tool execution: {tool_call.name}({args_str})]\n",
                    },
                )

            try:
                if tool_call.name == "list_directory":
                    dir_path = args_dict.get("directory_path", ".")
                    raw_result = list_directory(dir_path, project_root=project_root)
                elif tool_call.name == "read_file":
                    filepath = args_dict.get("filepath", "")
                    raw_result = read_file(filepath, project_root=project_root)
                elif tool_call.name == "list_git_commits":
                    max_count = args_dict.get("max_count", 10)
                    revision = args_dict.get("revision", "HEAD")
                    file_path = args_dict.get("file_path")
                    raw_result = list_git_commits(
                        project_root=project_root,
                        max_count=max_count,
                        revision=revision,
                        file_path=file_path,
                    )
                elif tool_call.name == "get_git_commit":
                    commit_id = args_dict.get("commit_id", "")
                    file_path = args_dict.get("file_path")
                    stat_only = bool(args_dict.get("stat_only", False))
                    raw_result = get_git_commit(
                        commit_id=commit_id,
                        project_root=project_root,
                        file_path=file_path,
                        stat_only=stat_only,
                    )
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
                if save:
                    err_line = (
                        raw_result.strip().splitlines()[0]
                        if raw_result
                        else "Tool execution failed"
                    )
                    commit_prefix = f"Commit {commit_sha[:7]} " if commit_sha else ""
                    session.send_response(
                        req_id,
                        conn,
                        result={
                            "status": "progress",
                            "message": f"{commit_prefix}tool call failed: {err_line}",
                            "error": True,
                        },
                    )

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

    return "".join(accumulator)


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

        if params.get("batch") or params.get("save"):
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

                    try:
                        chat = client.chats.create(
                            model=model, config=generation_config
                        )

                        content = _execute_review_stream(
                            self,
                            req_id,
                            conn,
                            chat,
                            prompt_text,
                            active_root,
                            verbose=verbose,
                            save=True,
                            commit_sha=commit_sha,
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

            with temporary_git_worktree(project_root, worktree_ref) as active_root:
                _execute_review_stream(
                    self,
                    req_id,
                    conn,
                    chat,
                    prompt_text,
                    active_root,
                    verbose=verbose,
                    save=False,
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
