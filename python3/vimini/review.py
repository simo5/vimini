import vim
import subprocess
import json
import shlex
import os
import re
from vimini import util
from vimini.handler import StreamBufferHandler, register_handler, unregister_handler
from vimini.common.util import get_project_config


class ReviewChannelHandler(StreamBufferHandler):
    """
    Handles channel responses from the agent server for code review requests.
    Supports interactive stream reviews and batch save reviews.
    """

    def __init__(self, req_id, buffer=None, is_batch=False):
        super().__init__(req_id, buffer=buffer, name_hint="Vimini Review")
        self.is_batch = is_batch

    def handle_response(self, result):
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
            self.finished = True
            return

        buf = self.get_buffer()
        if buf is None:
            return

        if status == "thought":
            thought_text = result.get("thought", "")
            verbose = result.get("verbose")
            self.handle_thought(thought_text, verbose=verbose)

        elif status == "tool_use_requested":
            self.handle_tool_request(result)

        elif status == "chunk":
            if not self.first_chunk:
                self.first_chunk = True
                util.write_to_buffer(
                    buf, "\n========== REVIEW START ==========\n", append_to_last=True
                )
            chunk_text = result.get("text", "")
            self.handle_chunk(chunk_text)

        elif status in ("completed", "terminated"):
            self.finished = True
            base_buffer_name = f"[{self.req_id}] Vimini Review"
            try:
                buf.name = base_buffer_name
            except Exception:
                pass
            if status == "completed":
                util.display_message("Review completed.")
            else:
                util.display_message("Review terminated.", history=True)

        elif status == "error":
            self.finished = True
            err_msg = result.get("error", "Unknown error")
            util.write_to_buffer(
                buf, f"\nError: {err_msg}\n", append_to_last=True, redraw=True
            )
            util.display_message(f"Error: {err_msg}", error=True)


def send_review_termination(req_id):
    unregister_handler(req_id)
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
    pr=None,
    mr=None,
    remote="origin",
):
    """
    Sends content to the Gemini API for a code review via the background agent.
    If 'pr' or 'mr' is provided, fetches the GitHub PR or GitLab MR branch first.
    If 'save' is True and 'git_objects' are provided, saves reviews to 'save_path'.
    """
    util.log_info(
        f"review({prompt}, git_objects='{git_objects}', security_focus={security_focus}, verbose={verbose}, save={save}, save_path='{save_path}', pr={pr}, mr={mr}, remote='{remote}')"
    )
    try:
        # Validate remote
        if remote is None or str(remote).strip() == "":
            remote = "origin"
        else:
            remote = str(remote).strip()

        if remote.startswith("-") or not re.match(r"^[a-zA-Z0-9_\-\./]+$", remote):
            util.display_message(
                f"Security error: Invalid remote name '{remote}'.", error=True
            )
            return

        # Handle GitHub PR or GitLab MR fetching
        pr_id = str(pr).strip() if pr is not None else None
        mr_id = str(mr).strip() if mr is not None else None
        worktree_ref = None

        if pr_id or mr_id:
            if pr_id and not pr_id.isdigit():
                util.display_message(
                    f"Error: PR number must be numeric, got '{pr_id}'.", error=True
                )
                return

            if mr_id and not mr_id.isdigit():
                util.display_message(
                    f"Error: MR number must be numeric, got '{mr_id}'.", error=True
                )
                return

            repo_path = util.get_git_repo_root()
            if not repo_path:
                return

            if pr_id:
                refspec = f"+refs/pull/{pr_id}/head:refs/vimini/pr/{pr_id}"
                local_ref = f"refs/vimini/pr/{pr_id}"
                service_desc = f"GitHub PR #{pr_id}"
            else:
                refspec = f"+refs/merge-requests/{mr_id}/head:refs/vimini/mr/{mr_id}"
                local_ref = f"refs/vimini/mr/{mr_id}"
                service_desc = f"GitLab MR #{mr_id}"

            project_name = util.get_git_repo_name() or os.path.basename(repo_path)
            worktree_config = get_project_config(
                "worktree", project_name=project_name, start_dir=repo_path
            )
            if worktree_config and str(worktree_config).strip():
                worktree_ref = local_ref

            util.display_message(f"Fetching {service_desc} from {remote}...")
            fetch_cmd = ["git", "-C", repo_path, "fetch", remote, refspec]
            res = subprocess.run(
                fetch_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if res.returncode != 0:
                err = (res.stderr or "git fetch failed.").strip()
                util.display_message(f"Git fetch error: {err}", error=True)
                return

            if not git_objects:
                base_target = None
                sym_cmd = [
                    "git",
                    "-C",
                    repo_path,
                    "symbolic-ref",
                    "--short",
                    f"refs/remotes/{remote}/HEAD",
                ]
                sym_res = subprocess.run(
                    sym_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                if sym_res.returncode == 0 and sym_res.stdout.strip():
                    base_target = sym_res.stdout.strip()
                else:
                    for candidate in [
                        f"{remote}/main",
                        f"{remote}/master",
                        "main",
                        "master",
                    ]:
                        check_cmd = [
                            "git",
                            "-C",
                            repo_path,
                            "rev-parse",
                            "--verify",
                            candidate,
                        ]
                        if (
                            subprocess.run(
                                check_cmd,
                                capture_output=True,
                                text=True,
                                encoding="utf-8",
                                errors="replace",
                                check=False,
                            ).returncode
                            == 0
                        ):
                            base_target = candidate
                            break

                if not base_target:
                    base_target = "HEAD"

                mb_cmd = [
                    "git",
                    "-C",
                    repo_path,
                    "merge-base",
                    base_target,
                    local_ref,
                ]
                mb_res = subprocess.run(
                    mb_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                if mb_res.returncode == 0 and mb_res.stdout.strip():
                    base_sha = mb_res.stdout.strip()
                    rev_cmd = ["git", "-C", repo_path, "rev-parse", local_ref]
                    rev_res = subprocess.run(
                        rev_cmd, capture_output=True, text=True, check=False
                    )
                    target_sha = (
                        rev_res.stdout.strip() if rev_res.returncode == 0 else ""
                    )
                    if base_sha != target_sha and target_sha:
                        git_objects = f"{base_sha}..{local_ref}"
                    else:
                        git_objects = local_ref
                else:
                    git_objects = local_ref

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
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

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

            handler = ReviewChannelHandler(job_id, is_batch=True)
            register_handler(job_id, handler)

            util.display_message("Processing batch review via agent... (Async)")

            req = {
                "jsonrpc": "2.0",
                "id": str(job_id),
                "method": "review",
                "params": {
                    "batch": True,
                    "save": True,
                    "prompt": prompt,
                    "security_focus": security_focus,
                    "verbose": verbose,
                    "project_root": repo_path,
                    "commit_list": commit_list,
                    "target_dir": target_dir,
                    "worktree_ref": worktree_ref,
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
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

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
        review_buf_num = getattr(review_buffer, "number", 1) or 1

        try:
            review_buffer.vars["vimini_job_id"] = str(job_id)
        except Exception as e:
            util.log_info(f"Error setting vimini_job_id on review buffer: {e}")

        handler = ReviewChannelHandler(job_id, buffer=review_buffer)
        register_handler(job_id, handler)

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
                "save": False,
                "prompt": prompt,
                "review_content": review_content,
                "content_source_description": content_source_description,
                "security_focus": security_focus,
                "verbose": verbose,
                "project_root": project_root,
                "worktree_ref": worktree_ref,
            },
        }

        util.send_channel_request(req)

    except FileNotFoundError:
        util.display_message(
            "Error: `git` command not found. Is it in your PATH?", error=True
        )
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)
