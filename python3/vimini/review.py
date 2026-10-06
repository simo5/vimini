import vim
import subprocess
import json
import shlex
import os
import re
from vimini import util

_STREAM_FIRST_CHUNK_MAP = set()


def _to_str(val):
    if isinstance(val, bytes):
        return val.decode("utf-8", errors="replace")
    return str(val) if val is not None else ""


def _find_buffer(req_id):
    try:
        for buf in vim.buffers:
            bid = buf.vars.get("vimini_job_id")
            if bid is not None and _to_str(bid) == str(req_id):
                return buf
    except Exception:
        pass
    return None


def handle_channel_response(req_id, result):
    """
    Handles channel responses from the agent server for review requests.
    Statuses: 'thought', 'chunk', 'completed', 'progress', 'batch_completed', 'error'
    """

    if not isinstance(result, dict):
        return

    status = result.get("status")

    if status == "progress":
        msg = result.get("message", "")
        is_err = result.get("error", False)
        util.display_message(msg, error=is_err, history=True)
        return

    if status == "batch_completed":
        msg = result.get("message", "All reviews completed and saved.")
        util.display_message(msg, history=True)
        return

    buf = _find_buffer(req_id)
    buf_num = buf.number if buf else None

    if status == "thought":
        if buf:
            try:
                if "[->G?]" in buf.name:
                    buf.name = buf.name.replace("[->G?]", "[<-G]")
                elif "[->G]" in buf.name:
                    buf.name = buf.name.replace("[->G]", "[<-G]")
            except Exception:
                pass
        thought_text = result.get("thought", "")
        verbose = result.get("verbose")
        if verbose is None:
            try:
                verbose = vim.eval("get(g:, 'vimini_thinking', 'on')") == "on"
            except Exception:
                verbose = True
        if verbose and thought_text and buf_num:
            util.append_to_buffer(buf_num, thought_text)

    elif status in ("chunk", "tool_use_requested"):
        if buf:
            try:
                spin_map = {
                    "[->G?]": "[<-G]",
                    "[->G]": "[<-G]",
                    "[<-G]": "[<-\\]",
                    "[<-\\]": "[<-|]",
                    "[<-|]": "[<-/]",
                    "[<-/]": "[<-G]",
                }
                for spin in spin_map.items():
                    if spin[0] in buf.name:
                        buf.name = buf.name.replace(spin[0], spin[1])
                        break
            except Exception:
                pass

        if buf_num:
            if req_id not in _STREAM_FIRST_CHUNK_MAP:
                _STREAM_FIRST_CHUNK_MAP.add(req_id)
                util.append_to_buffer(buf_num, "\n========== REVIEW START ==========\n")

            chunk_text = result.get("text", "")
            if chunk_text:
                util.append_to_buffer(buf_num, chunk_text)

    elif status in ("completed", "terminated"):
        _STREAM_FIRST_CHUNK_MAP.discard(req_id)
        if buf:
            base_buffer_name = f"[{req_id}] Vimini Review"
            try:
                buf.name = base_buffer_name
            except Exception:
                pass
        if status == "completed":
            util.display_message("Review completed.")
        else:
            util.display_message("Review terminated.", history=True)

    elif status == "error":
        _STREAM_FIRST_CHUNK_MAP.discard(req_id)
        err_msg = result.get("error", "Unknown error")
        if buf_num:
            util.append_to_buffer(buf_num, f"\nError: {err_msg}")
        util.display_message(f"Error: {err_msg}", error=True)


def send_review_termination(req_id):
    req = {
        "jsonrpc": "2.0",
        "id": str(req_id),
        "method": "review",
        "params": {"terminate": True},
    }
    util.send_channel_request(req, True)


def review(
    prompt,
    git_objects=None,
    security_focus=False,
    verbose=False,
    save=False,
    save_path=None,
):
    """
    Sends content to the Gemini API for a code review via the background agent.
    If 'save' is True and 'git_objects' are provided, saves reviews to 'save_path'.
    """
    util.log_info(
        f"review({prompt}, git_objects='{git_objects}', security_focus={security_focus}, verbose={verbose}, save={save}, save_path='{save_path}')"
    )
    try:
        # --- BATCH SAVE MODE ---
        if git_objects and save:
            repo_path = util.get_git_repo_root()
            if not repo_path:
                return

            objects_to_resolve = shlex.split(git_objects)
            for obj in objects_to_resolve:
                if obj.startswith("-"):
                    util.display_message(
                        "Security error: Git options (like flags starting with '-') are not allowed.",
                        error=True,
                    )
                    return

            # Check if a range is specified. If not, we don't want to walk the whole history.
            rev_list_args = []
            if not any(".." in obj for obj in objects_to_resolve):
                rev_list_args.append("--no-walk")

            cmd = (
                ["git", "-C", repo_path, "rev-list", "--reverse"]
                + rev_list_args
                + objects_to_resolve
            )
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if result.returncode != 0:
                error_message = (result.stderr or "git rev-list failed.").strip()
                util.display_message(f"Git error: {error_message}", error=True)
                return

            commit_list = [sha for sha in result.stdout.strip().split("\n") if sha]
            if not commit_list:
                util.display_message(
                    f"No commits found for range '{git_objects}'.", history=True
                )
                return

            # Determine Save Directory
            target_dir = repo_path
            path_config = save_path

            # If path not provided via argument, check global variable
            if not path_config:
                path_config = vim.eval("get(g:, 'vimini_review_path', '')")

            if path_config:
                expanded = os.path.expanduser(os.path.expandvars(path_config))
                # os.path.join handles absolute paths in the second argument by discarding the first
                target_dir = os.path.join(repo_path, expanded)

                if not os.path.exists(target_dir):
                    try:
                        os.makedirs(target_dir, exist_ok=True)
                    except Exception as e:
                        util.display_message(
                            f"Error creating directory {target_dir}: {e}", error=True
                        )
                        return

            job_name = f"Review batch: {git_objects} {prompt}"
            job_id = str(util.reserve_next_job_id(job_name))

            util.display_message("Processing batch review via agent... (Async)")

            req = {
                "jsonrpc": "2.0",
                "id": str(job_id),
                "method": "review",
                "params": {
                    "batch": True,
                    "prompt": prompt,
                    "security_focus": security_focus,
                    "verbose": verbose,
                    "project_root": repo_path,
                    "commit_list": commit_list,
                    "target_dir": target_dir,
                },
            }

            util.send_channel_request(req)
            return

        # --- INTERACTIVE MODE (ASYNC) ---
        review_content = ""
        content_source_description = ""
        project_root = util.get_git_repo_root() or os.getcwd()

        if git_objects:
            repo_path = util.get_git_repo_root()
            if not repo_path:
                return

            objects_to_show = shlex.split(git_objects)
            for obj in objects_to_show:
                if obj.startswith("-"):
                    util.display_message(
                        "Security error: Git options (like flags starting with '-') are not allowed.",
                        error=True,
                    )
                    return

            cmd = ["git", "-C", repo_path, "show"] + objects_to_show
            util.display_message(f"Running git show {git_objects}... ")
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if result.returncode != 0:
                error_message = (result.stderr or "git show failed.").strip()
                util.display_message(f"Git error: {error_message}", error=True)
                return
            review_content = result.stdout
            content_source_description = f"the output of `git show {git_objects}`"
        else:
            review_content = "\n".join(vim.current.buffer[:])
            original_filetype = vim.eval("&filetype") or "text"
            content_source_description = f"the following {original_filetype} code"

        if not review_content.strip():
            util.display_message("Nothing to review.", history=True)
            return

        job_name = (
            f"Review: {git_objects if git_objects else 'current buffer'} {prompt}"
        )
        job_id = str(util.reserve_next_job_id(job_name))

        util.new_split()
        base_buffer_name = f"[{job_id}] Vimini Review"
        safe_name = f"{base_buffer_name} [->G?]".replace(" ", "\\ ")
        vim.command(f"silent keepalt file {safe_name}")
        vim.command("setlocal buftype=nofile")
        vim.command("setlocal bufhidden=wipe")
        vim.command("setlocal noswapfile")
        vim.command("setlocal filetype=markdown")
        vim.command(
            "highlight default ViminiService ctermfg=Green guifg=Green cterm=italic gui=italic"
        )
        vim.command(
            "syntax match ViminiService '^\\[Agent requested tool execution: .*\\]'"
        )
        vim.command(
            f"autocmd BufUnload <buffer> py3 from vimini.review import send_review_termination; send_review_termination('{job_id}')"
        )

        review_buffer = vim.current.buffer
        review_buf_num = review_buffer.number

        review_buffer.vars["vimini_job_id"] = str(job_id)

        util.append_job_summary(review_buf_num, job_id, prompt, [])

        # Insert Git Diff Target if applicable
        if git_objects and review_content:
            separator_start = "========== GIT DIFF TARGET START =========="
            separator_end = "========== GIT DIFF TARGET END =========="
            util.append_to_buffer(
                review_buf_num,
                f"\n{separator_start}\n{review_content}\n{separator_end}\n",
            )

        util.display_message("Processing review via agent... (Async)")

        req = {
            "jsonrpc": "2.0",
            "id": str(job_id),
            "method": "review",
            "params": {
                "batch": False,
                "prompt": prompt,
                "review_content": review_content,
                "content_source_description": content_source_description,
                "security_focus": security_focus,
                "verbose": verbose,
                "project_root": project_root,
            },
        }

        util.send_channel_request(req)

    except FileNotFoundError:
        util.display_message(
            "Error: `git` command not found. Is it in your PATH?", error=True
        )
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)
