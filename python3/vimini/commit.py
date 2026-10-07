import vim
import os
import subprocess
import textwrap
import tempfile
from vimini import util
from vimini.util import get_model_name


def _run_format_command(repo_path):
    """
    Executes the configured project format-command prior to staging and committing.
    Returns True if formatting succeeded or no format command is set, False if formatting failed.
    """
    from vimini.common.util import get_project_tool_config, validate_tool_call

    format_config = get_project_tool_config("format", start_dir=repo_path)
    if not format_config:
        return True

    is_valid, cmd_or_err = validate_tool_call("format", {}, format_config)
    if not is_valid:
        util.display_message(f"Format command error: {cmd_or_err}", error=True)
        return False

    util.display_message(f"Running format command: {cmd_or_err}...")
    try:
        res = subprocess.run(
            cmd_or_err,
            shell=True,
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if res.returncode != 0:
            err_msg = (res.stderr or res.stdout).strip()
            util.display_message(
                f"Format command failed (exit code {res.returncode}): {err_msg}",
                error=True,
            )
            return False
        return True
    except Exception as e:
        util.display_message(f"Error running format command: {e}", error=True)
        return False


def _stage_changes(repo_path, message="Staging changes..."):
    """
    Stages changes with filtering (exclude dotfiles and swap/backup files).
    Returns True if successful, False otherwise.
    """
    if message:
        util.display_message(message)

    status_cmd = ["git", "-C", repo_path, "status", "-z", "--porcelain"]
    status_result = subprocess.run(
        status_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    files_to_add = []
    if status_result.returncode == 0:
        output = status_result.stdout
        i = 0
        n = len(output)
        while i < n:
            if i + 3 > n:
                break
            status = output[i : i + 2]
            path_start = i + 3
            path_end = output.find("\0", path_start)
            if path_end == -1:
                break

            path = output[path_start:path_end]
            i = path_end + 1

            if status[0] in ("R", "C"):
                orig_end = output.find("\0", i)
                if orig_end != -1:
                    i = orig_end + 1

            basename = os.path.basename(path)
            if (
                basename.startswith(".")
                or basename.endswith("~")
                or basename.endswith(".orig")
                or basename.endswith(".rej")
                or basename.endswith(".swp")
                or basename.endswith(".swo")
                or basename.endswith(".review.txt")
            ):
                continue

            files_to_add.append(path)

    if files_to_add:
        add_cmd = ["git", "-C", repo_path, "add", "--"] + files_to_add
        add_result = subprocess.run(
            add_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        if add_result.returncode != 0:
            error_message = (add_result.stderr or add_result.stdout).strip()
            util.display_message(f"Git add failed: {error_message}", error=True)
            return False

    return True


def handle_commit_response(req_id, result):
    text = result.get("text", "")
    repo_path = result.get("repo_path", "")
    diff_stat_output = result.get("diff_stat_output", "")
    regenerate = bool(result.get("regenerate", False))
    assistant = bool(result.get("assistant", True))

    response_text = text.strip()
    if "---" in response_text:
        parts = response_text.split("---", 1)
        subject = parts[0].strip()
        raw_body = parts[1].strip() if len(parts) > 1 else ""
    else:  # Fallback if model doesn't follow instructions.
        lines = response_text.split("\n")
        subject = lines[0].strip()
        raw_body = "\n".join(lines[1:]).strip()

    body = ""
    if raw_body:
        wrapped_lines = []
        for line in raw_body.split("\n"):
            if not line.strip():
                wrapped_lines.append("")
            else:
                wrapped_lines.extend(textwrap.wrap(line, width=78))
        body = "\n".join(wrapped_lines)

    if not subject:
        msg = "Failed to generate a commit message."
        if not regenerate and repo_path:
            msg += " Reverting `git add`."
            reset_cmd = ["git", "-C", repo_path, "reset", "HEAD", "--"]
            subprocess.run(reset_cmd, check=False)
        util.display_message(msg, error=True)
        return

    with tempfile.NamedTemporaryFile(
        mode="w+", delete=False, encoding="utf-8", suffix=".gitcommit"
    ) as f:
        tmp_filename = f.name

    util.new_split()
    vim.command(f"edit {tmp_filename.replace(' ', '\\ ')}")
    vim.command("setlocal filetype=gitcommit")
    vim.command("setlocal bufhidden=wipe")

    buffer_content = [subject, ""]
    if body:
        buffer_content.extend(body.split("\n"))

    if diff_stat_output:
        stat_header = (
            "# --- Files in commit ---" if regenerate else "# --- Staged files ---"
        )
        buffer_content.extend(["", stat_header])
        for line in diff_stat_output.split("\n"):
            buffer_content.append(f"# {line}")

    vim.current.buffer[:] = buffer_content

    safe_tmp = tmp_filename.replace("'", "''")
    safe_repo = repo_path.replace("'", "''")
    vim.command(f"let b:vimini_commit_tmp_file = '{safe_tmp}'")
    vim.command(f"let b:vimini_commit_repo_path = '{safe_repo}'")
    vim.command(f"let b:vimini_commit_regenerate = {1 if regenerate else 0}")
    vim.command(f"let b:vimini_commit_assistant = {1 if assistant else 0}")

    vim.command(
        "autocmd BufWipeout <buffer> py3 from vimini.commit import finalize_commit; finalize_commit()"
    )

    util.display_message(
        "Review the commit message. Save and close the buffer to commit, or close without saving to abort.",
        history=True,
    )
    vim.command("redraw!")


def commit(assistant=True, regenerate=False, amend=False, refinement=None):
    """
    Generates a commit message. By default, it stages all changes and creates
    a new commit. If `regenerate` is True, it regenerates the message for the
    HEAD commit and amends it. If `amend` is True, it stages changes, amends them
    into HEAD, and regenerates the message based on the new combined diff.
    Offloads commit message generation to the agent server.
    """
    util.log_info(
        f"commit(assistant={assistant}, regenerate={regenerate}, amend={amend}, refinement='{refinement}')"
    )
    try:
        repo_path = util.get_git_repo_root()
        if not repo_path:
            return  # Error handled by helper

        diff_to_process = ""
        diff_stat_output = ""

        if not regenerate:
            if not _run_format_command(repo_path):
                return

        if amend:
            if not _stage_changes(repo_path, "Staging changes for amend..."):
                return

            # Amend the code first
            amend_code_cmd = ["git", "-C", repo_path, "commit", "--amend", "--no-edit"]
            amend_code_result = subprocess.run(
                amend_code_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            if amend_code_result.returncode != 0:
                error_message = (
                    amend_code_result.stderr or amend_code_result.stdout
                ).strip()
                util.display_message(f"Git amend failed: {error_message}", error=True)
                return

            # Now regenerate commit message based on the new code
            regenerate = True

        if regenerate:
            util.display_message("Getting diff from HEAD...")
            diff_cmd = ["git", "-C", repo_path, "show", "--format="]
            diff_result = subprocess.run(
                diff_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            if diff_result.returncode != 0:
                error_message = (diff_result.stderr or "git show HEAD failed.").strip()
                util.display_message(f"Git error: {error_message}", error=True)
                return
            diff_to_process = diff_result.stdout.strip()

            stat_cmd = ["git", "-C", repo_path, "show", "--format=", "--stat"]
            stat_result = subprocess.run(
                stat_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if stat_result.returncode == 0:
                diff_stat_output = stat_result.stdout.strip()
        else:
            if not _stage_changes(repo_path, "Staging changes..."):
                return

            util.display_message("")

            staged_diff_cmd = ["git", "-C", repo_path, "diff", "--staged"]
            staged_diff_result = subprocess.run(
                staged_diff_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )

            if staged_diff_result.returncode != 0:
                error_message = staged_diff_result.stderr.strip()
                util.display_message(
                    f"Git error getting staged diff: {error_message}", error=True
                )
                return

            diff_to_process = staged_diff_result.stdout.strip()

            staged_stat_cmd = ["git", "-C", repo_path, "diff", "--staged", "--stat"]
            staged_stat_result = subprocess.run(
                staged_stat_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if staged_stat_result.returncode == 0:
                diff_stat_output = staged_stat_result.stdout.strip()

        if not diff_to_process:
            message = (
                "HEAD commit is empty. Nothing to regenerate."
                if regenerate
                else "No changes to commit."
            )
            util.display_message(message, history=True)
            return

        prompt = (
            "Based on the following git diff, generate a commit message with a subject and a body.\n\n"
            "RULES:\n"
            "1. The subject must be a single line, 50 characters or less, and summarize the change.\n"
            "2. Do not add any prefixes like 'feat:' or 'fix:' to the subject.\n"
            "3. The body should be a brief description of the changes, explaining the 'what' and 'why'.\n"
            "4. Separate the subject and body with '---' on its own line.\n"
            "5. Only output the raw text, with no extra explanations or markdown."
        )

        if refinement:
            prompt += f"\n\nADDITIONAL INSTRUCTIONS:\n{refinement}"

        prompt += f"\n\n--- GIT DIFF ---\n{diff_to_process}\n--- END GIT DIFF ---"

        util.display_message(
            "Generating commit message via agent server... (this may take a moment)"
        )

        req = {
            "jsonrpc": "2.0",
            "id": "commit",
            "method": "commit",
            "params": {
                "prompt": prompt,
                "repo_path": repo_path,
                "diff_stat_output": diff_stat_output,
                "regenerate": regenerate,
                "assistant": assistant,
            },
        }

        if not util.send_channel_request(req):
            msg = "Commit cancelled (sending request to agent server failed)."
            if not regenerate:
                msg += " Reverting `git add`."
                reset_cmd = ["git", "-C", repo_path, "reset", "HEAD", "--"]
                subprocess.run(reset_cmd, check=False)
            util.display_message(msg, error=True)

    except FileNotFoundError:
        util.display_message(
            "Error: `git` command not found. Is it in your PATH?", error=True
        )
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)


def finalize_commit():
    """Finalizes the commit after the user closes the commit message buffer."""
    util.log_info("Finalizing commit")
    try:
        tmp_filename = vim.eval("get(b:, 'vimini_commit_tmp_file', '')")
        repo_path = vim.eval("get(b:, 'vimini_commit_repo_path', '')")
        regenerate = int(vim.eval("get(b:, 'vimini_commit_regenerate', 0)"))
        assistant = int(vim.eval("get(b:, 'vimini_commit_assistant', 0)"))
    except Exception as e:
        util.log_info(f"Failed to read commit variables: {e}")
        return

    if not tmp_filename or not os.path.exists(tmp_filename):
        util.log_info(f"Failed to find commit log file")
        return

    try:
        with open(tmp_filename, "r", encoding="utf-8") as f:
            content = f.read()
        os.remove(tmp_filename)
    except Exception as e:
        util.display_message(f"Error reading commit message: {e}", error=True)
        return

    # Check if all lines are comments or empty
    has_non_comment = False
    for line in content.split("\n"):
        if line.strip() and not line.strip().startswith("#"):
            has_non_comment = True
            break

    if not has_non_comment:
        if regenerate:
            util.display_message(
                "Amend cancelled (no commit message saved).", error=True
            )
        else:
            util.display_message(
                "Commit cancelled (no commit message saved). Reverting `git add`.",
                error=True,
            )
            reset_cmd = ["git", "-C", repo_path, "reset", "HEAD", "--"]
            subprocess.run(reset_cmd, check=False)
        return

    util.log_info("Commit Message accepted")

    commit_cmd = ["git", "-C", repo_path, "commit", "-s", "--cleanup=strip"]
    if regenerate:
        commit_cmd.append("--amend")

    if assistant:
        trailer = f"Assisted-by: Gemini:{get_model_name()}"
        if trailer not in content:
            content += f"\n\n{trailer}\n"

    commit_cmd.extend(["-F", "-"])

    action = "Amending" if regenerate else "Committing"
    util.display_message(f"{action}...", history=True)
    vim.command("redraw")

    try:
        commit_result = subprocess.run(
            commit_cmd,
            input=content,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

        if commit_result.returncode == 0:
            success_message = commit_result.stdout.strip().split("\n")[0]
            action_past = "Amend" if regenerate else "Commit"
            util.display_message(
                f"{action_past} successful: {success_message}", history=True
            )
        else:
            error_message = (commit_result.stderr or commit_result.stdout).strip()
            action_past = "amend" if regenerate else "commit"
            util.display_message(
                f"Git {action_past} failed: {error_message}", error=True
            )
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)


_finalize_commit = finalize_commit
