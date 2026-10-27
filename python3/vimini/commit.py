import vim
import os
import re
import json
import subprocess
import textwrap
import tempfile
from vimini import util
from vimini.util import get_model_name
from vimini.handler import BaseChannelHandler, register_handler, unregister_handler

# In-memory store for commit files keyed by temporary commit filename
_commit_files_by_file = {}
_commit_meta_by_file = {}


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


def _revert_staged(repo_path):
    """Reverts git add / unstages changes."""
    if not repo_path:
        return
    res = subprocess.run(
        ["git", "-C", repo_path, "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        check=False,
    )
    if res.returncode == 0:
        reset_cmd = ["git", "-C", repo_path, "reset", "HEAD", "--"]
    else:
        reset_cmd = ["git", "-C", repo_path, "rm", "--cached", "-r", "--", "."]
    subprocess.run(reset_cmd, capture_output=True, check=False)


def _revert_amend(repo_path, orig_head):
    """Reverts an amended commit back to the original HEAD commit, restoring working tree changes."""
    if not repo_path or not orig_head:
        return
    reset_cmd = ["git", "-C", repo_path, "reset", orig_head]
    subprocess.run(reset_cmd, capture_output=True, check=False)


def _clean_rename_path(p):
    p = p.strip().strip('"').strip("'")
    cleaned = os.path.normpath(p).replace("\\", "/").lstrip("/")
    return cleaned if cleaned != "." else ""


def _extract_rename_paths(file_part):
    """Extracts (source, target) file paths from a diff stat rename spec."""
    if "=>" not in file_part:
        return None, None
    # Strip trailing similarity percentage e.g. " (100%)"
    file_part = re.sub(r"\s*\(\d+%\)$", "", file_part.strip())
    if "{" in file_part and "}" in file_part:
        prefix, rest = file_part.split("{", 1)
        rename_spec, suffix = rest.split("}", 1)
        if "=>" in rename_spec:
            old_part, new_part = rename_spec.split("=>", 1)
            source = _clean_rename_path(f"{prefix}{old_part.strip()}{suffix}")
            target = _clean_rename_path(f"{prefix}{new_part.strip()}{suffix}")
            return (source or None, target or None)
    parts = file_part.split("=>", 1)
    source = _clean_rename_path(parts[0].strip())
    target = _clean_rename_path(parts[1].strip())
    return (source or None, target or None)


def _match_body(body, commit_files):
    """Checks if body or diff-stat file_part in body matches commit_files. Returns matched filename or None."""
    if not body:
        return None
    if body in commit_files:
        return body
    if "|" in body:
        file_part = body.rsplit("|", 1)[0].strip()
        if file_part in commit_files:
            return file_part
        if "=>" in file_part:
            source, target = _extract_rename_paths(file_part)
            if target and target in commit_files:
                return target
            if source and source in commit_files:
                return source
    return None


def parse_file_line(line, commit_files):
    """
    Parses a line to check if it represents a file from commit_files.
    Returns (filename, is_deselected) if it matches a file in commit_files,
    or (None, None) if it does not.
    The only valid deselection form is exactly '#- '.
    """
    if not line or not line.startswith("#"):
        return None, None

    if not isinstance(commit_files, (set, frozenset)):
        commit_files = set(commit_files)

    # Exactly '#- ' indicates a deselected file
    if line.startswith("#- "):
        body = line[3:].lstrip()
        matched = _match_body(body, commit_files)
        if matched:
            return matched, True
        return None, None

    rest = line[1:]
    stripped_rest = rest.lstrip()
    if not stripped_rest:
        return None, None

    # Check if line matches directly (included file)
    matched = _match_body(stripped_rest, commit_files)
    if matched:
        return matched, False

    return None, None


def _get_commit_files_for_buffer():
    """Retrieves the list of commit files for the current buffer."""
    try:
        raw = vim.eval("get(b:, 'vimini_commit_files', '')")
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    try:
        tmp_filename = vim.eval("get(b:, 'vimini_commit_tmp_file', '')")
        return _commit_files_by_file.get(tmp_filename, [])
    except Exception:
        pass
    return []


def _get_amend_files_for_buffer():
    """Retrieves the list of files to amend for the current buffer, if defined."""
    try:
        raw = vim.eval("get(b:, 'vimini_commit_amend_files', '')")
        if raw:
            return set(json.loads(raw))
    except Exception:
        pass
    try:
        tmp_filename = vim.eval("get(b:, 'vimini_commit_tmp_file', '')")
        meta = _commit_meta_by_file.get(tmp_filename, {})
        if "amend_files" in meta:
            return set(meta["amend_files"])
    except Exception:
        pass
    return None


def _is_pure_regenerate_buffer():
    """Checks if the current commit buffer is in pure regenerate mode (regenerate and not amend)."""
    try:
        raw_regen = vim.eval("get(b:, 'vimini_commit_regenerate', 0)")
        raw_amend = vim.eval("get(b:, 'vimini_commit_amend', 0)")
        regen = int(raw_regen) if raw_regen else 0
        amend = int(raw_amend) if raw_amend else 0
        if regen and not amend:
            return True
        if regen == 0 and amend == 0:
            tmp_filename = vim.eval("get(b:, 'vimini_commit_tmp_file', '')")
            meta = _commit_meta_by_file.get(tmp_filename, {})
            if meta.get("regenerate") and not meta.get("amend"):
                return True
    except Exception:
        pass
    return False


def toggle_file():
    """
    Toggles file inclusion (marked with #- or readded) for the file under the cursor in the commit buffer.
    """
    try:
        commit_files = _get_commit_files_for_buffer()
        if not commit_files:
            return

        cursor = vim.current.window.cursor
        row, col = cursor[0], cursor[1]  # row is 1-indexed

        if row < 1 or row > len(vim.current.buffer):
            return

        line = vim.current.buffer[row - 1]
        commit_files_set = set(commit_files)
        fn, is_deselected = parse_file_line(line, commit_files_set)
        if fn is None:
            return

        new_line = None
        if is_deselected:
            # Re-add file (turn into included)
            if line.startswith("#-  "):
                new_line = "#   " + line[4:]
            else:
                new_line = "#  " + line[3:]
        else:
            if _is_pure_regenerate_buffer():
                util.display_message(
                    "Files cannot be deselected in regenerate mode", error=True
                )
                return

            files_to_amend = _get_amend_files_for_buffer()
            if files_to_amend is not None and fn not in files_to_amend:
                util.display_message(
                    "This file is not being changed by the amendment", error=True
                )
                return

            # Deselect file (mark with exactly #- )
            if line.startswith("#  "):
                new_line = "#- " + line[3:]
            else:
                new_line = "#- " + line[2:]

        vim.current.buffer[row - 1] = new_line
        new_col = min(col, len(new_line) - 1 if new_line else 0)
        vim.current.window.cursor = (row, new_col)
        vim.command("redraw")
    except Exception as e:
        util.log_info(f"toggle_file error: {e}")


class CommitChannelHandler(BaseChannelHandler):
    """
    Handles channel responses for commit message generation requests.
    """

    def __init__(
        self,
        req_id,
        files_to_commit=None,
        orig_head=None,
        amend=False,
        regenerate=False,
        repo_path=None,
    ):
        super().__init__(req_id)
        self.files_to_commit = files_to_commit or []
        self.orig_head = orig_head
        self.amend = amend
        self.regenerate = regenerate
        self.repo_path = repo_path

    def handle_response(self, result):
        self.finished = True
        if not result.get("commit_files") and self.files_to_commit:
            result["commit_files"] = self.files_to_commit
        if not result.get("orig_head") and self.orig_head:
            result["orig_head"] = self.orig_head
        if "amend" not in result and self.amend:
            result["amend"] = self.amend
        if "regenerate" not in result and self.regenerate:
            result["regenerate"] = self.regenerate
        if not result.get("repo_path") and self.repo_path:
            result["repo_path"] = self.repo_path
        try:
            handle_commit_response(self.req_id, result)
        except Exception as e:
            if self.amend and self.orig_head and self.repo_path:
                _revert_amend(self.repo_path, self.orig_head)
            elif not result.get("regenerate") and self.repo_path:
                _revert_staged(self.repo_path)
            util.display_message(f"Error: {e}", error=True)

    def handle_error(self, error):
        self.finished = True
        err_msg = (
            error.get("message", "Unknown error")
            if isinstance(error, dict)
            else str(error)
        )
        if self.amend and self.orig_head and self.repo_path:
            _revert_amend(self.repo_path, self.orig_head)
        elif not self.amend and not self.regenerate and self.repo_path:
            _revert_staged(self.repo_path)
        util.display_message(f"Error: {err_msg}", error=True)


def handle_commit_response(req_id, result):
    text = result.get("text", "")
    repo_path = result.get("repo_path", "")
    diff_stat_output = result.get("diff_stat_output", "")
    commit_files = result.get("commit_files") or []
    regenerate = bool(result.get("regenerate", False))
    amend = bool(result.get("amend", False))
    orig_head = result.get("orig_head") or ""
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
        if amend and orig_head and repo_path:
            msg += " Reverting amend."
            _revert_amend(repo_path, orig_head)
        elif not regenerate and repo_path:
            msg += " Reverting `git add`."
            _revert_staged(repo_path)
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

    rename_pairs = []
    if diff_stat_output or commit_files:
        stat_header = "# --- Diff stat ---"
        buffer_content.extend(["", stat_header])
        if not (regenerate and not amend):
            buffer_content.append("# (Press <Space> to toggle file inclusion)")
        if diff_stat_output:
            files_to_amend = []
            commit_files_set = set(commit_files)
            seen_files = set()
            for line in diff_stat_output.split("\n"):
                if not line.strip():
                    continue
                fn, _ = parse_file_line(f"#   {line.strip()}", commit_files_set)
                if fn:
                    seen_files.add(fn)
                    if "|" in line:
                        file_part = line.rsplit("|", 1)[0].strip()
                        if "=>" in file_part:
                            source, target = _extract_rename_paths(file_part)
                            if source:
                                seen_files.add(source)
                            if target:
                                seen_files.add(target)
                            if source and target:
                                rename_pairs.append((source, target))
                    buffer_content.append(f"#   {line.strip()}")
                else:
                    buffer_content.append(f"#  {line.strip()}")
            for fn in commit_files:
                if fn not in seen_files:
                    buffer_content.append(f"#   {fn}")
        else:
            for fn in commit_files:
                buffer_content.append(f"#   {fn}")

        if regenerate and not amend:
            files_to_amend = []
        elif amend:
            files_to_amend = []
            if orig_head and repo_path:
                try:
                    res = subprocess.run(
                        [
                            "git",
                            "-C",
                            repo_path,
                            "diff-tree",
                            "--no-commit-id",
                            "--name-only",
                            "-r",
                            "-z",
                            orig_head,
                            "HEAD",
                        ],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                        check=False,
                    )
                    if res.returncode == 0 and res.stdout:
                        files_to_amend = [f for f in res.stdout.split("\0") if f]
                except Exception:
                    files_to_amend = []
            for s, t in rename_pairs:
                if s in files_to_amend or t in files_to_amend:
                    if s not in files_to_amend:
                        files_to_amend.append(s)
                    if t not in files_to_amend:
                        files_to_amend.append(t)
        else:
            files_to_amend = list(commit_files)

    vim.current.buffer[:] = buffer_content

    safe_tmp = tmp_filename.replace("'", "''")
    safe_repo = repo_path.replace("'", "''")
    safe_orig_head = orig_head.replace("'", "''")
    safe_files = json.dumps(commit_files).replace("'", "''")
    safe_renames = json.dumps(rename_pairs).replace("'", "''")
    safe_amend_files = json.dumps(files_to_amend).replace("'", "''")
    vim.command(f"let b:vimini_commit_tmp_file = '{safe_tmp}'")
    vim.command(f"let b:vimini_commit_repo_path = '{safe_repo}'")
    vim.command(f"let b:vimini_commit_regenerate = {1 if regenerate else 0}")
    vim.command(f"let b:vimini_commit_amend = {1 if amend else 0}")
    vim.command(f"let b:vimini_commit_orig_head = '{safe_orig_head}'")
    vim.command(f"let b:vimini_commit_assistant = {1 if assistant else 0}")
    vim.command(f"let b:vimini_commit_files = '{safe_files}'")
    vim.command(f"let b:vimini_commit_renames = '{safe_renames}'")
    vim.command(f"let b:vimini_commit_amend_files = '{safe_amend_files}'")

    _commit_files_by_file[tmp_filename] = list(commit_files)
    _commit_meta_by_file[tmp_filename] = {
        "files": list(commit_files),
        "orig_head": orig_head,
        "amend": amend,
        "regenerate": regenerate,
        "repo_path": repo_path,
        "renames": rename_pairs,
        "amend_files": list(files_to_amend),
    }

    vim.command("nnoremap <buffer> <silent> <Space> :call ViminiCommitToggleFile()<CR>")
    vim.command(
        "autocmd BufWipeout <buffer> py3 from vimini.commit import finalize_commit; finalize_commit()"
    )

    if regenerate and not amend:
        review_msg = "Review the commit message. Save and close the buffer to commit, or close without saving to abort."
    else:
        review_msg = "Review the commit message. Toggle files with <Space>. Save and close the buffer to commit, or close without saving to abort."

    util.display_message(
        review_msg,
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
        files_to_commit = []
        orig_head = ""

        if not regenerate:
            if not _run_format_command(repo_path):
                return

        if amend:
            head_res = subprocess.run(
                ["git", "-C", repo_path, "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if head_res.returncode != 0:
                util.display_message("Git error: failed to resolve HEAD.", error=True)
                return
            orig_head = head_res.stdout.strip()

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
                _revert_staged(repo_path)
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
                if amend and orig_head:
                    _revert_amend(repo_path, orig_head)
                util.display_message(f"Git error: {error_message}", error=True)
                return
            diff_to_process = diff_result.stdout.strip()

            stat_cmd = [
                "git",
                "-c",
                "core.quotepath=false",
                "-C",
                repo_path,
                "show",
                "--format=",
                "--stat",
                "--stat-width=1000",
                "--stat-name-width=1000",
                "--stat-graph-width=10",
            ]
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

            files_cmd = [
                "git",
                "-C",
                repo_path,
                "diff-tree",
                "--no-commit-id",
                "--name-only",
                "-r",
                "-z",
                "--root",
                "HEAD",
            ]
            files_result = subprocess.run(
                files_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if files_result.returncode == 0 and files_result.stdout:
                files_to_commit = [f for f in files_result.stdout.split("\0") if f]
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

            staged_stat_cmd = [
                "git",
                "-c",
                "core.quotepath=false",
                "-C",
                repo_path,
                "diff",
                "--staged",
                "--stat",
                "--stat-width=1000",
                "--stat-name-width=1000",
                "--stat-graph-width=10",
            ]
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

            staged_files_cmd = [
                "git",
                "-C",
                repo_path,
                "diff",
                "--staged",
                "--name-only",
                "-z",
            ]
            staged_files_result = subprocess.run(
                staged_files_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if staged_files_result.returncode == 0 and staged_files_result.stdout:
                files_to_commit = [
                    f for f in staged_files_result.stdout.split("\0") if f
                ]

        if not diff_to_process:
            if amend and orig_head:
                _revert_amend(repo_path, orig_head)
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

        job_id = str(util.reserve_next_job_id("Commit"))

        handler = CommitChannelHandler(
            job_id,
            files_to_commit=files_to_commit,
            orig_head=orig_head,
            amend=amend,
            regenerate=regenerate,
            repo_path=repo_path,
        )
        register_handler(job_id, handler)

        req = {
            "jsonrpc": "2.0",
            "id": job_id,
            "method": "commit",
            "params": {
                "prompt": prompt,
                "repo_path": repo_path,
                "diff_stat_output": diff_stat_output,
                "commit_files": files_to_commit,
                "regenerate": regenerate,
                "amend": amend,
                "orig_head": orig_head,
                "assistant": assistant,
            },
        }

        if not util.send_channel_request(req):
            unregister_handler(job_id)
            if amend and orig_head:
                _revert_amend(repo_path, orig_head)
                msg = "Amend cancelled (sending request to agent server failed). Reverting amend."
            elif not regenerate:
                _revert_staged(repo_path)
                msg = "Commit cancelled (sending request to agent server failed). Reverting `git add`."
            else:
                msg = "Commit cancelled (sending request to agent server failed)."
            util.display_message(msg, error=True)

    except FileNotFoundError:
        if amend and orig_head:
            _revert_amend(repo_path, orig_head)
        elif repo_path and not regenerate:
            _revert_staged(repo_path)
        util.display_message(
            "Error: `git` command not found. Is it in your PATH?", error=True
        )
    except Exception as e:
        if amend and orig_head:
            _revert_amend(repo_path, orig_head)
        elif repo_path and not regenerate:
            _revert_staged(repo_path)
        util.display_message(f"Error: {e}", error=True)


def finalize_commit():
    """Finalizes the commit after the user closes the commit message buffer."""
    util.log_info("Finalizing commit")
    try:
        tmp_filename = vim.eval("get(b:, 'vimini_commit_tmp_file', '')")
        repo_path = vim.eval("get(b:, 'vimini_commit_repo_path', '')")
        raw_regen = vim.eval("get(b:, 'vimini_commit_regenerate', 0)")
        regenerate = int(raw_regen) if raw_regen else 0
        raw_amend = vim.eval("get(b:, 'vimini_commit_amend', 0)")
        amend = int(raw_amend) if raw_amend else 0
        raw_orig_head = vim.eval("get(b:, 'vimini_commit_orig_head', '')")
        orig_head = str(raw_orig_head) if raw_orig_head else ""
        raw_assistant = vim.eval("get(b:, 'vimini_commit_assistant', 0)")
        assistant = int(raw_assistant) if raw_assistant else 0
        raw_files = vim.eval("get(b:, 'vimini_commit_files', '')")
        commit_files = json.loads(raw_files) if raw_files else []
        raw_renames = vim.eval("get(b:, 'vimini_commit_renames', '')")
        rename_pairs = json.loads(raw_renames) if raw_renames else []
    except Exception as e:
        util.log_info(f"Failed to read commit variables: {e}")
        return

    meta = _commit_meta_by_file.pop(tmp_filename, {})
    if not commit_files:
        commit_files = meta.get("files", _commit_files_by_file.pop(tmp_filename, []))
    else:
        _commit_files_by_file.pop(tmp_filename, None)
    if not orig_head:
        orig_head = meta.get("orig_head", "")
    if not amend:
        amend = meta.get("amend", 0)
    if not repo_path:
        repo_path = meta.get("repo_path", "")
    if not regenerate:
        regenerate = meta.get("regenerate", 0)
    if not rename_pairs:
        rename_pairs = meta.get("renames", [])

    if not tmp_filename or not os.path.exists(tmp_filename):
        util.log_info("Failed to find commit log file")
        if amend and orig_head and repo_path:
            _revert_amend(repo_path, orig_head)
        return

    try:
        with open(tmp_filename, "r", encoding="utf-8") as f:
            content = f.read()
        os.remove(tmp_filename)
    except Exception as e:
        if amend and orig_head and repo_path:
            _revert_amend(repo_path, orig_head)
        util.display_message(f"Error reading commit message: {e}", error=True)
        return

    # Check if all lines are comments or empty
    has_non_comment = False
    for line in content.split("\n"):
        if line.strip() and not line.strip().startswith("#"):
            has_non_comment = True
            break

    if not has_non_comment:
        if amend and orig_head:
            util.display_message(
                "Amend cancelled (no commit message saved). Reverting amend.",
                error=True,
            )
            _revert_amend(repo_path, orig_head)
        elif regenerate:
            util.display_message(
                "Amend cancelled (no commit message saved).", error=True
            )
        else:
            util.display_message(
                "Commit cancelled (no commit message saved). Reverting `git add`.",
                error=True,
            )
            _revert_staged(repo_path)
        return

    # Parse file lines to find deselected files (marked with exactly #- )
    deselected_files = []
    if commit_files:
        stat_header = "# --- Diff stat ---"
        if stat_header not in content:
            if amend and orig_head:
                util.display_message(
                    "Amend cancelled (commit was malformed: missing diff stat header). Reverting amend.",
                    error=True,
                )
                _revert_amend(repo_path, orig_head)
            elif regenerate:
                util.display_message(
                    "Amend cancelled (commit was malformed: missing diff stat header).",
                    error=True,
                )
            else:
                util.display_message(
                    "Commit cancelled (commit was malformed: missing diff stat header). Reverting `git add`.",
                    error=True,
                )
                _revert_staged(repo_path)
            return

        commit_files_set = set(commit_files)
        seen_included = set()
        stat_content = content.split(stat_header, 1)[1]
        for line in stat_content.split("\n"):
            fn, is_deselected = parse_file_line(line, commit_files_set)
            if fn:
                rename_source, rename_target = None, None
                if is_deselected:
                    body = line[3:].lstrip()
                else:
                    body = line[1:].lstrip()
                if "|" in body:
                    file_part = body.rsplit("|", 1)[0].strip()
                    if "=>" in file_part:
                        rename_source, rename_target = _extract_rename_paths(file_part)

                if is_deselected:
                    if fn not in deselected_files:
                        deselected_files.append(fn)
                    if rename_source and rename_source not in deselected_files:
                        deselected_files.append(rename_source)
                    if rename_target and rename_target not in deselected_files:
                        deselected_files.append(rename_target)
                else:
                    seen_included.add(fn)
                    if rename_source:
                        seen_included.add(rename_source)
                    if rename_target:
                        seen_included.add(rename_target)

        if regenerate and not amend:
            if deselected_files or any(fn not in seen_included for fn in commit_files):
                util.display_message(
                    "Files cannot be deselected in regenerate mode", error=True
                )
                return
        else:
            for fn in commit_files:
                if fn not in seen_included and fn not in deselected_files:
                    deselected_files.append(fn)

            for s, t in rename_pairs:
                if s in deselected_files or t in deselected_files:
                    if s not in deselected_files:
                        deselected_files.append(s)
                    if t not in deselected_files:
                        deselected_files.append(t)

        remaining_files = [f for f in commit_files if f not in deselected_files]
        if not remaining_files:
            if amend and orig_head:
                util.display_message(
                    "Amend cancelled (no files selected to commit). Reverting amend.",
                    error=True,
                )
                _revert_amend(repo_path, orig_head)
            elif regenerate:
                util.display_message(
                    "Amend cancelled (no files selected to commit).",
                    error=True,
                )
            else:
                util.display_message(
                    "Commit cancelled (no files selected to commit). Reverting `git add`.",
                    error=True,
                )
                _revert_staged(repo_path)
            return

        if deselected_files:
            try:
                util.log_info(
                    f"Removing deselected files from commit: {deselected_files}"
                )
                if amend and orig_head:
                    unstage_cmd = [
                        "git",
                        "-C",
                        repo_path,
                        "reset",
                        orig_head,
                        "--",
                    ] + deselected_files
                elif regenerate:
                    head_parent_check = subprocess.run(
                        ["git", "-C", repo_path, "rev-parse", "--verify", "HEAD~1"],
                        capture_output=True,
                        check=False,
                    )
                    if head_parent_check.returncode == 0:
                        unstage_cmd = [
                            "git",
                            "-C",
                            repo_path,
                            "reset",
                            "HEAD~1",
                            "--",
                        ] + deselected_files
                    else:
                        unstage_cmd = [
                            "git",
                            "-C",
                            repo_path,
                            "rm",
                            "--cached",
                            "--",
                        ] + deselected_files
                else:
                    head_check = subprocess.run(
                        ["git", "-C", repo_path, "rev-parse", "--verify", "HEAD"],
                        capture_output=True,
                        check=False,
                    )
                    if head_check.returncode == 0:
                        unstage_cmd = [
                            "git",
                            "-C",
                            repo_path,
                            "reset",
                            "HEAD",
                            "--",
                        ] + deselected_files
                    else:
                        unstage_cmd = [
                            "git",
                            "-C",
                            repo_path,
                            "rm",
                            "--cached",
                            "--",
                        ] + deselected_files

                unstage_res = subprocess.run(
                    unstage_cmd,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                )
                if unstage_res.returncode != 0:
                    err = (unstage_res.stderr or unstage_res.stdout).strip()
                    util.display_message(
                        f"Failed to unstage deselected files: {err}", error=True
                    )
                    if amend and orig_head:
                        _revert_amend(repo_path, orig_head)
                    elif not regenerate and repo_path:
                        _revert_staged(repo_path)
                    elif regenerate and repo_path:
                        subprocess.run(
                            ["git", "-C", repo_path, "reset", "HEAD", "--"],
                            capture_output=True,
                            check=False,
                        )
                    return
            except Exception as e:
                util.display_message(
                    f"Error unstaging deselected files: {e}", error=True
                )
                if amend and orig_head:
                    _revert_amend(repo_path, orig_head)
                elif not regenerate and repo_path:
                    _revert_staged(repo_path)
                elif regenerate and repo_path:
                    subprocess.run(
                        ["git", "-C", repo_path, "reset", "HEAD", "--"],
                        capture_output=True,
                        check=False,
                    )
                return

    util.log_info("Commit Message accepted")

    commit_cmd = ["git", "-C", repo_path, "commit", "-s", "--cleanup=strip"]
    if regenerate:
        commit_cmd.append("--amend")

    if assistant:
        trailer = f"Assisted-by: {get_model_name()}"
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
            if amend and orig_head:
                _revert_amend(repo_path, orig_head)
            elif regenerate and repo_path:
                subprocess.run(
                    ["git", "-C", repo_path, "reset", "HEAD", "--"],
                    capture_output=True,
                    check=False,
                )
            util.display_message(
                f"Git {action_past} failed: {error_message}", error=True
            )
    except Exception as e:
        if amend and orig_head:
            _revert_amend(repo_path, orig_head)
        elif regenerate and repo_path:
            subprocess.run(
                ["git", "-C", repo_path, "reset", "HEAD", "--"],
                capture_output=True,
                check=False,
            )
        elif repo_path:
            _revert_staged(repo_path)
        util.display_message(f"Error: {e}", error=True)


_finalize_commit = finalize_commit
