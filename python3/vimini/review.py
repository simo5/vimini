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
                self.reset_turn_state()
                util.write_to_buffer(
                    buf, "\n========== REVIEW START ==========\n", append_to_last=True
                )
            chunk_text = result.get("text", "")
            self.handle_chunk(chunk_text)

        elif status in ("completed", "terminated"):
            self.finished = True
            self.reset_turn_state()
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
            self.reset_turn_state()
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


def resolve_remote(repo_path, remote=None):
    """
    Determines the git remote to use.
    If 'remote' is explicitly specified and non-empty, returns it.
    If 'g:vimini_default_remote' is configured and non-empty, returns it (or warns if invalid).
    Otherwise, inspects available remotes in repo_path:
    - If 'upstream' is present in remotes, returns 'upstream' (preferred for fork workflows).
    - Finds the canonical remote for the current branch:
      1. branch.<current_branch>.remote
      2. Upstream tracking branch remote (@{upstream})
    - Falls back to default branch configurations:
      3. Default branch remote (branch.main.remote, branch.master.remote)
    - If no configured remote is found, falls back to available remotes:
      4. 'origin' (if present in remotes)
      5. First available remote in remotes
      6. Fallback to 'origin' if no remotes are available.
    """
    if remote is not None and str(remote).strip() != "":
        return str(remote).strip()

    try:
        cfg_remote = vim.eval("get(g:, 'vimini_default_remote', '')")
        if isinstance(cfg_remote, str) and cfg_remote.strip():
            candidate = cfg_remote.strip()
            if not candidate.startswith("-") and re.match(
                r"^[a-zA-Z0-9_\-\./]+$", candidate
            ):
                return candidate
            util.display_message(
                f"Warning: Invalid 'g:vimini_default_remote' setting '{candidate}'.",
                error=True,
            )
    except Exception:
        pass

    if not repo_path:
        return "origin"

    # Find available remotes
    remotes = []
    try:
        res = subprocess.run(
            ["git", "-C", repo_path, "remote"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if res.returncode == 0:
            remotes = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except Exception:
        pass

    # In fork setups, PRs/MRs reside on upstream; prefer it early to avoid overhead
    if "upstream" in remotes:
        return "upstream"

    # Find canonical remote for current branch
    try:
        branch_res = subprocess.run(
            ["git", "-C", repo_path, "symbolic-ref", "--short", "-q", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        current_branch = branch_res.stdout.strip() if branch_res.returncode == 0 else ""
        if current_branch:
            # 1. branch.<name>.remote
            cfg_res = subprocess.run(
                [
                    "git",
                    "-C",
                    repo_path,
                    "config",
                    f"branch.{current_branch}.remote",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            branch_remote = cfg_res.stdout.strip() if cfg_res.returncode == 0 else ""
            if (
                branch_remote
                and branch_remote != "."
                and (not remotes or branch_remote in remotes)
            ):
                return branch_remote

            # 2. @{upstream} tracking remote
            u_res = subprocess.run(
                [
                    "git",
                    "-C",
                    repo_path,
                    "rev-parse",
                    "--abbrev-ref",
                    "--symbolic-full-name",
                    "@{upstream}",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            if u_res.returncode == 0 and u_res.stdout.strip():
                u_remote = u_res.stdout.strip().split("/")[0]
                if u_remote and u_remote in remotes:
                    return u_remote
    except Exception:
        pass

    # 3. Fallback to main/master branch remote config
    try:
        for def_branch in ["main", "master"]:
            def_res = subprocess.run(
                [
                    "git",
                    "-C",
                    repo_path,
                    "config",
                    f"branch.{def_branch}.remote",
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            def_remote = def_res.stdout.strip() if def_res.returncode == 0 else ""
            if (
                def_remote
                and def_remote != "."
                and (not remotes or def_remote in remotes)
            ):
                return def_remote
    except Exception:
        pass

    if "origin" in remotes:
        return "origin"
    if remotes:
        return remotes[0]

    return "origin"


# Maximum number of remote branches inspected when looking for a PR/MR base branch.
MAX_BASE_CANDIDATES = 50


def _git(repo_path, *args):
    """Runs a git command in 'repo_path' and returns the CompletedProcess."""
    return subprocess.run(
        ["git", "-C", repo_path, *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def _short_ref(ref, remote):
    """Strips the 'refs/remotes/<remote>/' or '<remote>/' prefix, if present, from 'ref'."""
    for prefix in (f"refs/remotes/{remote}/", f"{remote}/"):
        if ref.startswith(prefix):
            return ref[len(prefix) :]
    return ref


def get_remote_default_branch(repo_path, remote):
    """
    Returns the remote-tracking ref of the default branch of 'remote' when it can
    be determined locally, otherwise None.
    """
    sym_res = _git(repo_path, "symbolic-ref", f"refs/remotes/{remote}/HEAD")
    if sym_res.returncode == 0 and sym_res.stdout.strip():
        return sym_res.stdout.strip()
    for name in ("main", "master"):
        ref = f"refs/remotes/{remote}/{name}"
        if _git(repo_path, "rev-parse", "--verify", "--quiet", ref).returncode == 0:
            return ref
    return None


def list_remote_branches(repo_path, remote, limit=MAX_BASE_CANDIDATES):
    """
    Returns the remote-tracking branches of 'remote', most recently updated
    first, capped at 'limit' entries. Symbolic refs pointing at the remote's
    default branch (refs/remotes/<remote>/HEAD) are skipped.
    """
    prefix = f"refs/remotes/{remote}/"
    res = _git(
        repo_path,
        "for-each-ref",
        "--sort=-committerdate",
        "--format=%(refname)",
        prefix,
    )
    if res.returncode != 0:
        return []

    branches = []
    for line in res.stdout.splitlines():
        ref = line.strip()
        if not ref.startswith(prefix) or ref == f"{prefix}HEAD":
            continue
        branches.append(ref)
        if limit is not None and len(branches) >= limit:
            break
    return branches


def resolve_pr_base(repo_path, remote, pr_ref, default_ref=None):
    """
    Determines which branch a PR/MR was opened against, without consulting the
    forge API and without relying on the remote's HEAD symref alone.

    Every remote-tracking branch of 'remote' is compared with the pulled head and
    the branch sharing the most recent fork point with it -- that is, the one
    leaving the fewest of the PR/MR's own commits on top -- becomes the base.
    This identifies PRs/MRs that target a non-default branch, which inspecting
    the remote's HEAD symref alone would get wrong. Branches that already contain
    the pulled head are ignored because they cannot be its base, and the remote's
    default branch wins ties so that the common case stays predictable.

    The fork point of the winning branch is returned along with it, so callers
    reuse that commit instead of recomputing it.

    Returns a (base_ref, base_sha, commit_count) tuple, or (None, None, None) when
    no branch can serve as a base.
    """
    branches = list_remote_branches(repo_path, remote)
    if default_ref and default_ref not in branches:
        branches.append(default_ref)
    if not branches:
        return None, None, None

    best_key = None
    best = None
    for idx, ref in enumerate(branches):
        mb_res = _git(repo_path, "merge-base", ref, pr_ref)
        base_sha = mb_res.stdout.strip() if mb_res.returncode == 0 else ""
        if not base_sha:
            continue
        count_res = _git(repo_path, "rev-list", "--count", f"{base_sha}..{pr_ref}")
        if count_res.returncode != 0:
            continue
        out = count_res.stdout.strip()
        if not out.isdigit():
            continue
        count = int(out)
        if count <= 0:
            # The branch already contains the PR head, so it is not its base.
            continue
        # Fewest PR/MR commits on top of the fork point first, then the default
        # branch, then candidate list order (most recently updated first).
        key = (count, 0 if ref == default_ref else 1, idx)
        if best_key is None or key < best_key:
            best_key = key
            best = (ref, base_sha, count)

    if best is None:
        return None, None, None
    return best


def _build_pr_review_range(repo_path, remote, pr_ref, service_desc):
    """
    Returns the git revision range covering exactly the commits of a PR/MR.

    The remote is refreshed first so that the base branch is current, then the
    base branch is resolved locally by resolve_pr_base(). The remote's default
    branch is used as a fallback for repositories where no branch can be ranked.
    The resolved base and commit count are reported so that an unexpectedly large
    review is immediately visible to the user.

    Returns None when the PR/MR contributes no commits of its own, for example
    when it is empty or has already been merged. Callers must treat that as
    "nothing to review" and stop, instead of sending a commit that belongs to the
    base branch to the model.
    """
    util.display_message(
        f"Refreshing '{remote}' to determine the {service_desc} base branch..."
    )
    res = _git(repo_path, "fetch", "--no-tags", "--", remote)
    if res.returncode != 0:
        err = (res.stderr or "git fetch failed.").strip()
        util.display_message(
            f"Warning: could not refresh '{remote}': {err}. "
            "The review range may include unrelated commits.",
            error=True,
        )

    default_ref = get_remote_default_branch(repo_path, remote)
    base_ref, base_sha, commit_count = resolve_pr_base(
        repo_path, remote, pr_ref, default_ref=default_ref
    )

    if base_ref is None and default_ref is not None:
        # No branch could be ranked (for example in a single-branch clone):
        # fall back to the remote's default branch.
        mb_res = _git(repo_path, "merge-base", default_ref, pr_ref)
        if mb_res.returncode == 0 and mb_res.stdout.strip():
            base_ref = default_ref
            base_sha = mb_res.stdout.strip()

    if base_ref is None:
        for candidate in (f"{remote}/main", f"{remote}/master", "main", "master"):
            exists = _git(repo_path, "rev-parse", "--verify", "--quiet", candidate)
            if exists.returncode != 0:
                continue
            mb_res = _git(repo_path, "merge-base", candidate, pr_ref)
            if mb_res.returncode == 0 and mb_res.stdout.strip():
                base_ref = candidate
                base_sha = mb_res.stdout.strip()
                break

    if base_ref is None or not base_sha:
        util.display_message(
            f"Warning: could not determine the base branch of {service_desc}; "
            "reviewing only its tip commit.",
            error=True,
        )
        return pr_ref

    # An empty or already merged PR/MR has no commits of its own: the fork point
    # coincides with the pulled head. Report it and return None so the caller
    # stops, rather than reviewing a commit that belongs to the base branch.
    rev_res = _git(repo_path, "rev-parse", pr_ref)
    target_sha = rev_res.stdout.strip() if rev_res.returncode == 0 else ""
    if target_sha and base_sha == target_sha:
        util.display_message(
            f"Nothing to review: {service_desc} has no commits on top of "
            f"'{_short_ref(base_ref, remote)}'.",
            history=True,
        )
        return None

    if commit_count is None:
        count_res = _git(repo_path, "rev-list", "--count", f"{base_sha}..{pr_ref}")
        count_out = count_res.stdout.strip() if count_res.returncode == 0 else ""
        commit_count = int(count_out) if count_out.isdigit() else None

    if commit_count is not None and commit_count <= 0:
        util.display_message(
            f"Nothing to review: {service_desc} has no commits on top of "
            f"'{_short_ref(base_ref, remote)}'.",
            history=True,
        )
        return None

    base_name = _short_ref(base_ref, remote)
    if commit_count is None:
        util.display_message(f"Reviewing {service_desc} (base: {base_name}).")
    else:
        util.display_message(
            f"Reviewing {commit_count} commit(s) of {service_desc} (base: {base_name})."
        )

    return f"{base_sha}..{pr_ref}"


def review(
    prompt,
    git_objects=None,
    security_focus=False,
    verbose=False,
    save=False,
    save_path=None,
    pr=None,
    mr=None,
    remote=None,
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

            remote = resolve_remote(repo_path, remote)
            if (
                not remote
                or remote.startswith("-")
                or not re.match(r"^[a-zA-Z0-9_\-\./]+$", remote)
            ):
                util.display_message(
                    f"Security error: Invalid remote name '{remote}'.", error=True
                )
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
            res = _git(repo_path, "fetch", "--", remote, refspec)
            if res.returncode != 0:
                err = (res.stderr or "git fetch failed.").strip()
                util.display_message(f"Git fetch error: {err}", error=True)
                return

            if not git_objects:
                git_objects = _build_pr_review_range(
                    repo_path, remote, local_ref, service_desc
                )
                if not git_objects:
                    # Empty or already merged PR/MR: there is nothing of its own
                    # to review, and falling through would review the base
                    # branch tip instead.
                    return

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
            try:
                max_turns_val = vim.eval("get(g:, 'vimini_review_max_turns', '')")
                if (
                    isinstance(max_turns_val, (int, str))
                    and str(max_turns_val).isdigit()
                ):
                    req["params"]["max_turns"] = int(max_turns_val)
            except Exception:
                pass

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
        vim.command("highlight default ViminiThinking ctermfg=DarkGray guifg=DarkGray")
        vim.command(
            "highlight default ViminiService ctermfg=Green guifg=Green cterm=italic gui=italic"
        )
        vim.command("syntax match ViminiThinking '^T>.*'")
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
        try:
            max_turns_val = vim.eval("get(g:, 'vimini_review_max_turns', '')")
            if isinstance(max_turns_val, (int, str)) and str(max_turns_val).isdigit():
                req["params"]["max_turns"] = int(max_turns_val)
        except Exception:
            pass

        util.send_channel_request(req)

    except FileNotFoundError:
        util.display_message(
            "Error: `git` command not found. Is it in your PATH?", error=True
        )
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)
