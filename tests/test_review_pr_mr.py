import sys
import os
import pytest
import subprocess
from unittest.mock import MagicMock, patch

# Ensure python3 root is in sys.path
sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

import vimini.review as review_module
from vimini.review import (
    resolve_remote,
    resolve_pr_base,
    get_remote_default_branch,
    _short_ref,
)
from vimini.common.util import temporary_git_worktree, resolve_worktree_dir


def _fake_git(
    remotes="origin\n",
    default_branch="refs/remotes/origin/main",
    branches=("refs/remotes/origin/main",),
    commit_counts=None,
    merge_bases=None,
    exists=(),
    fetch_returncode=0,
    fetch_stderr="",
    merge_base="sha_base",
    target_sha="sha_pr",
    show_output="diff_content\n",
    show_returncode=0,
):
    """
    Builds a subprocess.run side effect that answers git invocations based on
    the git subcommand, so tests do not depend on the exact call sequence.
    """
    commit_counts = commit_counts or {}
    merge_bases = merge_bases or {}
    branches = list(branches)
    exists = set(exists)

    def fake_run(cmd, *args, **kwargs):
        gargs = list(cmd[3:]) if cmd[:2] == ["git", "-C"] else list(cmd)
        sub = gargs[0] if gargs else ""

        if sub == "remote":
            return MagicMock(returncode=0, stdout=remotes, stderr="")
        if sub == "symbolic-ref":
            if any("refs/remotes/" in a for a in gargs):
                if default_branch is None:
                    return MagicMock(returncode=1, stdout="", stderr="")
                return MagicMock(returncode=0, stdout=f"{default_branch}\n", stderr="")
            # Current branch: report a detached HEAD for these tests.
            return MagicMock(returncode=1, stdout="", stderr="")
        if sub == "for-each-ref":
            return MagicMock(
                returncode=0, stdout="".join(f"{b}\n" for b in branches), stderr=""
            )
        if sub == "rev-list":
            count = commit_counts.get(gargs[-1])
            if count is None:
                return MagicMock(returncode=128, stdout="", stderr="unknown revision")
            return MagicMock(returncode=0, stdout=f"{count}\n", stderr="")
        if sub == "fetch":
            return MagicMock(
                returncode=fetch_returncode, stdout="", stderr=fetch_stderr
            )
        if sub == "merge-base":
            # Allow per-branch merge bases, keyed by the compared branch ref.
            looked_up = merge_bases.get(gargs[1], merge_base)
            if looked_up is None:
                return MagicMock(returncode=1, stdout="", stderr="")
            return MagicMock(returncode=0, stdout=f"{looked_up}\n", stderr="")
        if sub == "rev-parse":
            if "--verify" in gargs:
                if gargs[-1] in exists:
                    return MagicMock(returncode=0, stdout="sha\n", stderr="")
                return MagicMock(returncode=1, stdout="", stderr="")
            if target_sha is None:
                return MagicMock(returncode=1, stdout="", stderr="")
            return MagicMock(returncode=0, stdout=f"{target_sha}\n", stderr="")
        if sub == "show":
            return MagicMock(returncode=show_returncode, stdout=show_output, stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    return fake_run


def _git_commands(mock_run):
    """Returns the list of git argument lists issued through a mocked run."""
    return [call[0][0] for call in mock_run.call_args_list]


def test_review_github_pr_success(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("vimini.review.get_project_config", return_value=".var/vimini/worktrees"),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.send_channel_request") as mock_send,
        patch("vimini.util.new_split"),
        patch("vim.command"),
    ):
        mock_run.side_effect = _fake_git(
            commit_counts={"sha_base..refs/vimini/pr/123": 2}
        )

        review_module.review("Review this PR", pr="123", remote="origin")

        cmds = _git_commands(mock_run)

        # The PR head is fetched with '--' before positional arguments.
        assert [
            "git",
            "-C",
            repo_path,
            "fetch",
            "--",
            "origin",
            "+refs/pull/123/head:refs/vimini/pr/123",
        ] in cmds

        # The remote is refreshed so the base branch is current.
        assert ["git", "-C", repo_path, "fetch", "--no-tags", "--", "origin"] in cmds

        # The base branch is resolved against the pulled head.
        assert [
            "git",
            "-C",
            repo_path,
            "merge-base",
            "refs/remotes/origin/main",
            "refs/vimini/pr/123",
        ] in cmds

        show_cmd = ["git", "-C", repo_path, "show", "sha_base..refs/vimini/pr/123"]
        assert show_cmd in cmds

        # Verify request sent to channel
        mock_send.assert_called_once()
        req_params = mock_send.call_args[0][0]["params"]
        assert req_params["review_content"] == "diff_content\n"
        assert req_params["worktree_ref"] == "refs/vimini/pr/123"


def test_review_gitlab_mr_success(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("vimini.review.get_project_config", return_value=".var/vimini/worktrees"),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.send_channel_request") as mock_send,
        patch("vimini.util.new_split"),
        patch("vim.command"),
    ):
        mock_run.side_effect = _fake_git(
            default_branch="refs/remotes/upstream/main",
            branches=("refs/remotes/upstream/main",),
            commit_counts={"sha_base..refs/vimini/mr/456": 1},
        )

        review_module.review("Review MR", mr="456", remote="upstream", git_objects=None)

        cmds = _git_commands(mock_run)
        assert [
            "git",
            "-C",
            repo_path,
            "fetch",
            "--",
            "upstream",
            "+refs/merge-requests/456/head:refs/vimini/mr/456",
        ] in cmds
        assert [
            "git",
            "-C",
            repo_path,
            "merge-base",
            "refs/remotes/upstream/main",
            "refs/vimini/mr/456",
        ] in cmds
        mock_send.assert_called_once()
        req_params = mock_send.call_args[0][0]["params"]
        assert req_params["review_content"] == "diff_content\n"
        assert req_params["worktree_ref"] == "refs/vimini/mr/456"


def test_review_pr_invalid_number():
    with (
        patch("vimini.util.display_message") as mock_display,
        patch("subprocess.run") as mock_run,
    ):
        review_module.review("test", pr="123;rm -rf")
        mock_display.assert_called_once()
        assert "numeric" in mock_display.call_args[0][0]
        mock_run.assert_not_called()


def test_review_pr_invalid_remote(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("vimini.util.display_message") as mock_display,
        patch("subprocess.run") as mock_run,
    ):
        review_module.review("test", pr="123", remote="-oProxyCommand=calc")
        mock_display.assert_called_once()
        assert "Invalid remote name" in mock_display.call_args[0][0]
        mock_run.assert_not_called()


def test_review_pr_auto_resolved_invalid_remote(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("vimini.review.resolve_remote", return_value="-oProxyCommand=calc"),
        patch("vimini.util.display_message") as mock_display,
        patch("subprocess.run") as mock_run,
    ):
        review_module.review("test", pr="123", remote=None)
        mock_display.assert_called()
        err_msgs = [
            call[0][0]
            for call in mock_display.call_args_list
            if "Security error: Invalid remote name" in call[0][0]
        ]
        assert len(err_msgs) == 1
        mock_run.assert_not_called()


def test_review_pr_auto_resolved_invalid_characters(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("vimini.review.resolve_remote", return_value="remote;rm -rf"),
        patch("vimini.util.display_message") as mock_display,
        patch("subprocess.run") as mock_run,
    ):
        review_module.review("test", pr="123", remote=None)
        mock_display.assert_called()
        err_msgs = [
            call[0][0]
            for call in mock_display.call_args_list
            if "Security error: Invalid remote name" in call[0][0]
        ]
        assert len(err_msgs) == 1
        mock_run.assert_not_called()


def test_review_pr_fetch_failure(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.display_message") as mock_display,
    ):
        mock_run.return_value = MagicMock(
            returncode=128, stdout="", stderr="remote not found"
        )
        review_module.review("test", pr="999")
        mock_display.assert_called()
        err_msgs = [
            call[0][0]
            for call in mock_display.call_args_list
            if "Git fetch error" in call[0][0]
        ]
        assert len(err_msgs) == 1
        assert "remote not found" in err_msgs[0]


def test_review_pr_without_worktree_config(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("vimini.review.get_project_config", return_value=None),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.send_channel_request") as mock_send,
        patch("vimini.util.new_split"),
        patch("vim.command"),
    ):
        mock_run.side_effect = _fake_git(
            commit_counts={"sha_base..refs/vimini/pr/123": 1}
        )

        review_module.review("Review this PR", pr="123", remote="origin")

        mock_send.assert_called_once()
        req_params = mock_send.call_args[0][0]["params"]
        assert req_params.get("worktree_ref") is None


def test_review_subprocess_non_utf8_handling(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.send_channel_request") as mock_send,
        patch("vimini.util.new_split"),
        patch("vim.command"),
    ):
        mock_run.side_effect = _fake_git(
            commit_counts={"sha_base..refs/vimini/pr/123": 1},
            # Simulate stdout with replaced non-utf8 characters (\ufffd)
            show_output="diff with binary byte: \ufffd\n",
        )

        review_module.review("Review non-utf8 PR", pr="123", remote="origin")

        # Every git invocation must decode with replacement semantics.
        for call in mock_run.call_args_list:
            assert call.kwargs.get("encoding") == "utf-8"
            assert call.kwargs.get("errors") == "replace"

        # Verify request sent to channel with replacement character preserved
        mock_send.assert_called_once()
        req_params = mock_send.call_args[0][0]["params"]
        assert req_params["review_content"] == "diff with binary byte: \ufffd\n"


def test_review_max_turns_from_vim_variable(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.send_channel_request") as mock_send,
        patch("vimini.util.new_split"),
        patch("vim.command"),
        patch(
            "vim.eval",
            side_effect=lambda expr: (
                "40" if expr == "get(g:, 'vimini_review_max_turns', '')" else "python"
            ),
        ),
    ):
        mock_run.side_effect = _fake_git(
            commit_counts={"sha_base..refs/vimini/pr/123": 1}
        )

        review_module.review("Review with custom turns", pr="123", remote="origin")

        mock_send.assert_called_once()
        req_params = mock_send.call_args[0][0]["params"]
        assert req_params["max_turns"] == 40


def test_temporary_git_worktree_none_or_empty_ref():
    with temporary_git_worktree("/some/path", None) as path:
        assert path == "/some/path"
    with temporary_git_worktree("/some/path", "") as path:
        assert path == "/some/path"


def test_resolve_worktree_dir():
    assert resolve_worktree_dir(None) is None
    assert resolve_worktree_dir("") is None
    # Arbitrary dir starting with .var without repo_path is no longer expanded to ~/.var
    assert resolve_worktree_dir(".var/vimini/worktrees") is None
    assert resolve_worktree_dir(".var/vimini/worktree") is None
    # Relative paths without repo_path return None
    assert resolve_worktree_dir("my_worktree") is None
    # Relative paths with repo_path are supported
    assert resolve_worktree_dir("my_worktree", repo_path="/repo") == os.path.realpath(
        "/repo/my_worktree"
    )
    assert resolve_worktree_dir(
        ".var/vimini/worktree", repo_path="/repo"
    ) == os.path.realpath("/repo/.var/vimini/worktree")
    expected_worktree = os.path.realpath(os.path.expanduser("~/.var/vimini/worktree"))
    assert resolve_worktree_dir("~/.var/vimini/worktree") == expected_worktree
    assert resolve_worktree_dir("/abs/path") == os.path.realpath("/abs/path")


def test_temporary_git_worktree_not_allowed_when_not_set(tmp_path):
    repo_dir = str(tmp_path)
    with patch("vimini.common.util.get_project_config", return_value=None):
        with temporary_git_worktree(repo_dir, "refs/heads/foo") as path:
            # When worktree option is not set, worktrees are not allowed and yields repo_dir
            assert path == repo_dir


def test_temporary_git_worktree_failure_fallback(tmp_path):
    repo_dir = str(tmp_path)
    with (
        patch("subprocess.run") as mock_run,
        patch("vimini.common.util.get_project_config", return_value="/tmp/some_wt"),
    ):
        mock_run.return_value = MagicMock(returncode=1, stderr="fatal: not a git repo")
        with temporary_git_worktree(repo_dir, "refs/heads/foo") as path:
            assert path == repo_dir


def test_temporary_git_worktree_real_repo(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=str(repo_dir), check=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=str(repo_dir), check=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo_dir),
        check=True,
    )

    # Create initial commit on main
    file1 = repo_dir / "file1.txt"
    file1.write_text("main content\n")
    subprocess.run(["git", "add", "file1.txt"], cwd=str(repo_dir), check=True)
    subprocess.run(
        ["git", "commit", "-m", "initial commit"], cwd=str(repo_dir), check=True
    )

    # Create feature branch with a new file
    subprocess.run(["git", "checkout", "-b", "feature"], cwd=str(repo_dir), check=True)
    file2 = repo_dir / "file2.txt"
    file2.write_text("feature content\n")
    subprocess.run(["git", "add", "file2.txt"], cwd=str(repo_dir), check=True)
    subprocess.run(
        ["git", "commit", "-m", "feature commit"], cwd=str(repo_dir), check=True
    )

    # Switch back to main
    subprocess.run(["git", "checkout", "main"], cwd=str(repo_dir), check=True)
    assert not os.path.exists(str(repo_dir / "file2.txt"))

    wt_base = tmp_path / "custom_worktrees"
    wt_base.mkdir()
    # Create temporary worktree pointing to feature
    captured_worktree_path = None
    with temporary_git_worktree(
        str(repo_dir), "feature", worktree_dir=str(wt_base)
    ) as worktree_path:
        captured_worktree_path = worktree_path
        assert os.path.isdir(worktree_path)
        assert worktree_path != str(repo_dir)
        # Worktree path must be inside wt_base (not in /tmp)
        assert str(wt_base) in worktree_path
        # Feature file must be present in the worktree
        assert os.path.exists(os.path.join(worktree_path, "file2.txt"))
        # Main directory must remain untouched without feature file
        assert not os.path.exists(str(repo_dir / "file2.txt"))

    # Worktree path must be cleaned up after exit
    assert captured_worktree_path is not None
    assert not os.path.exists(captured_worktree_path)


def test_resolve_remote_explicit():
    # Explicit remote argument always takes precedence without running git
    with patch("subprocess.run") as mock_run:
        assert resolve_remote("/repo", remote="gh") == "gh"
        mock_run.assert_not_called()


def test_resolve_remote_g_vimini_default_remote():
    with (
        patch(
            "vim.eval",
            side_effect=lambda expr: (
                "gh" if expr == "get(g:, 'vimini_default_remote', '')" else ""
            ),
        ),
        patch("subprocess.run") as mock_run,
    ):
        assert resolve_remote("/repo", remote=None) == "gh"
        mock_run.assert_not_called()


def test_resolve_remote_g_vimini_default_remote_invalid():
    mock_remote = MagicMock(returncode=0, stdout="origin\n", stderr="")
    with (
        patch(
            "vim.eval",
            side_effect=lambda expr: (
                "-oProxyCommand=calc"
                if expr == "get(g:, 'vimini_default_remote', '')"
                else ""
            ),
        ),
        patch("subprocess.run", return_value=mock_remote),
        patch("vimini.util.display_message") as mock_display,
    ):
        assert resolve_remote("/repo", remote=None) == "origin"
        mock_display.assert_called_once()
        assert "Invalid 'g:vimini_default_remote'" in mock_display.call_args[0][0]


def test_resolve_remote_origin_available():
    mock_remote = MagicMock(returncode=0, stdout="gh\norigin\n", stderr="")
    mock_branch = MagicMock(returncode=1, stdout="", stderr="")  # detached HEAD
    mock_main_remote = MagicMock(returncode=1, stdout="", stderr="")
    mock_master_remote = MagicMock(returncode=1, stdout="", stderr="")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            mock_remote,
            mock_branch,
            mock_main_remote,
            mock_master_remote,
        ]
        assert resolve_remote("/repo", remote=None) == "origin"


def test_resolve_remote_origin_not_available_branch_tracking():
    mock_remote = MagicMock(returncode=0, stdout="gh\n", stderr="")
    mock_branch = MagicMock(returncode=0, stdout="main\n", stderr="")
    mock_cfg = MagicMock(returncode=0, stdout="gh\n", stderr="")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [mock_remote, mock_branch, mock_cfg]
        assert resolve_remote("/repo", remote=None) == "gh"

        assert mock_run.call_args_list[0][0][0] == ["git", "-C", "/repo", "remote"]
        assert mock_run.call_args_list[1][0][0] == [
            "git",
            "-C",
            "/repo",
            "symbolic-ref",
            "--short",
            "-q",
            "HEAD",
        ]
        assert mock_run.call_args_list[2][0][0] == [
            "git",
            "-C",
            "/repo",
            "config",
            "branch.main.remote",
        ]


def test_resolve_remote_origin_not_available_upstream_tracking():
    mock_remote = MagicMock(returncode=0, stdout="gh\n", stderr="")
    mock_branch = MagicMock(returncode=0, stdout="feature\n", stderr="")
    mock_cfg = MagicMock(returncode=1, stdout="", stderr="")
    mock_u = MagicMock(returncode=0, stdout="gh/feature\n", stderr="")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [mock_remote, mock_branch, mock_cfg, mock_u]
        assert resolve_remote("/repo", remote=None) == "gh"


def test_resolve_remote_branch_tracking_dot_ignored():
    # When branch.<name>.remote is "." (local tracking), it must be ignored and not returned
    mock_remote = MagicMock(returncode=0, stdout="origin\n", stderr="")
    mock_branch = MagicMock(returncode=0, stdout="feature\n", stderr="")
    mock_cfg = MagicMock(returncode=0, stdout=".\n", stderr="")
    mock_u = MagicMock(returncode=1, stdout="", stderr="")
    mock_main_remote = MagicMock(returncode=1, stdout="", stderr="")
    mock_master_remote = MagicMock(returncode=1, stdout="", stderr="")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            mock_remote,
            mock_branch,
            mock_cfg,
            mock_u,
            mock_main_remote,
            mock_master_remote,
        ]
        assert resolve_remote("/repo", remote=None) == "origin"


def test_resolve_remote_branch_tracking_dot_with_empty_remotes():
    mock_remote = MagicMock(returncode=0, stdout="", stderr="")
    mock_branch = MagicMock(returncode=0, stdout="feature\n", stderr="")
    mock_cfg = MagicMock(returncode=0, stdout=".\n", stderr="")
    mock_u = MagicMock(returncode=1, stdout="", stderr="")
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [mock_remote, mock_branch, mock_cfg, mock_u]
        assert resolve_remote("/repo", remote=None) == "origin"


def test_resolve_remote_upstream_tracking_local_branch_ignored():
    # When rev-parse @{upstream} returns a local branch name (not in remotes), ignore it
    mock_remote = MagicMock(returncode=0, stdout="origin\n", stderr="")
    mock_branch = MagicMock(returncode=0, stdout="feature\n", stderr="")
    mock_cfg = MagicMock(returncode=1, stdout="", stderr="")
    mock_u = MagicMock(
        returncode=0, stdout="main\n", stderr=""
    )  # tracking local 'main'
    mock_main_remote = MagicMock(returncode=1, stdout="", stderr="")
    mock_master_remote = MagicMock(returncode=1, stdout="", stderr="")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            mock_remote,
            mock_branch,
            mock_cfg,
            mock_u,
            mock_main_remote,
            mock_master_remote,
        ]
        # 'main' is not in remotes, so it should fall back to 'origin'
        assert resolve_remote("/repo", remote=None) == "origin"


def test_resolve_remote_upstream_tracking_empty_remotes_ignored():
    # When remotes is empty, @{upstream} should not be used as remote
    mock_remote = MagicMock(returncode=0, stdout="", stderr="")
    mock_branch = MagicMock(returncode=0, stdout="feature\n", stderr="")
    mock_cfg = MagicMock(returncode=1, stdout="", stderr="")
    mock_u = MagicMock(returncode=0, stdout="origin/feature\n", stderr="")
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [mock_remote, mock_branch, mock_cfg, mock_u]
        assert resolve_remote("/repo", remote=None) == "origin"


def test_resolve_remote_push_configs_ignored():
    # Push configurations (pushRemote, remote.pushDefault) should not be used for PR/MR fetching
    mock_remote = MagicMock(returncode=0, stdout="origin\n", stderr="")
    mock_branch = MagicMock(returncode=0, stdout="feature\n", stderr="")
    mock_cfg = MagicMock(returncode=1, stdout="", stderr="")
    mock_u = MagicMock(returncode=1, stdout="", stderr="")
    mock_main_remote = MagicMock(returncode=1, stdout="", stderr="")
    mock_master_remote = MagicMock(returncode=1, stdout="", stderr="")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            mock_remote,
            mock_branch,
            mock_cfg,
            mock_u,
            mock_main_remote,
            mock_master_remote,
        ]
        assert resolve_remote("/repo", remote=None) == "origin"
        cmds = [call[0][0] for call in mock_run.call_args_list]
        assert not any("pushRemote" in " ".join(cmd) for cmd in cmds)
        assert not any("pushDefault" in " ".join(cmd) for cmd in cmds)


def test_resolve_remote_origin_not_available_main_branch_remote():
    mock_remote = MagicMock(returncode=0, stdout="gh\nother\n", stderr="")
    mock_branch = MagicMock(returncode=1, stdout="", stderr="")  # detached HEAD
    mock_main_remote = MagicMock(returncode=0, stdout="gh\n", stderr="")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [mock_remote, mock_branch, mock_main_remote]
        assert resolve_remote("/repo", remote=None) == "gh"


def test_resolve_remote_origin_not_available_single_remote():
    mock_remote = MagicMock(returncode=0, stdout="gh\n", stderr="")
    mock_branch = MagicMock(returncode=1, stdout="", stderr="")  # detached HEAD
    mock_main_remote = MagicMock(returncode=1, stdout="", stderr="")
    mock_master_remote = MagicMock(returncode=1, stdout="", stderr="")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [
            mock_remote,
            mock_branch,
            mock_main_remote,
            mock_master_remote,
        ]
        assert resolve_remote("/repo", remote=None) == "gh"


def test_resolve_remote_origin_not_available_upstream_preference():
    mock_remote = MagicMock(returncode=0, stdout="fork\nupstream\n", stderr="")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [mock_remote]
        assert resolve_remote("/repo", remote=None) == "upstream"
        # Immediate return when upstream remote is detected without extra subprocesses
        assert mock_run.call_count == 1


def test_resolve_remote_no_remotes():
    mock_remote = MagicMock(returncode=1, stdout="", stderr="")
    with patch("subprocess.run", return_value=mock_remote):
        assert resolve_remote("/repo", remote=None) == "origin"


def test_resolve_remote_fork_workflow_prefers_upstream():
    mock_remote = MagicMock(returncode=0, stdout="origin\nupstream\n", stderr="")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [mock_remote]
        # In a fork repository, upstream is preferred for PR/MR reviews
        assert resolve_remote("/repo", remote=None) == "upstream"
        assert mock_run.call_count == 1


def test_resolve_remote_fork_workflow_origin_tracking_overridden_by_upstream():
    # Even if current branch tracks origin, upstream remote takes precedence for reviews
    mock_remote = MagicMock(returncode=0, stdout="origin\nupstream\n", stderr="")

    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = [mock_remote]
        assert resolve_remote("/repo", remote=None) == "upstream"
        assert mock_run.call_count == 1


def test_resolve_remote_fork_workflow_explicit_override():
    # User can explicitly request origin or any other remote via argument
    with patch("subprocess.run") as mock_run:
        assert resolve_remote("/repo", remote="origin") == "origin"
        mock_run.assert_not_called()


def test_review_github_pr_auto_detect_gh_remote(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("vimini.review.get_project_config", return_value=None),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.send_channel_request") as mock_send,
        patch("vimini.util.new_split"),
        patch("vim.command"),
    ):
        mock_run.side_effect = _fake_git(
            remotes="gh\n",
            default_branch="refs/remotes/gh/main",
            branches=("refs/remotes/gh/main",),
            commit_counts={"sha_base..refs/vimini/pr/789": 3},
        )

        # Call review without remote argument
        review_module.review("Review PR auto-detect", pr="789")

        cmds = _git_commands(mock_run)
        assert [
            "git",
            "-C",
            repo_path,
            "fetch",
            "--",
            "gh",
            "+refs/pull/789/head:refs/vimini/pr/789",
        ] in cmds
        assert ["git", "-C", repo_path, "fetch", "--no-tags", "--", "gh"] in cmds
        mock_send.assert_called_once()


def test_resolve_pr_base_picks_branch_with_fewest_commits():
    # The PR targets 'release-1'; using 'main' would pull in unrelated commits.
    pr_ref = "refs/vimini/pr/1"
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = _fake_git(
            branches=("refs/remotes/origin/release-1", "refs/remotes/origin/main"),
            merge_bases={
                "refs/remotes/origin/release-1": "sha_release",
                "refs/remotes/origin/main": "sha_main",
            },
            commit_counts={
                f"sha_release..{pr_ref}": 2,
                f"sha_main..{pr_ref}": 9,
            },
        )
        base_ref, base_sha, commit_count = resolve_pr_base(
            "/repo",
            "origin",
            pr_ref,
            default_ref="refs/remotes/origin/main",
        )

    assert base_ref == "refs/remotes/origin/release-1"
    assert base_sha == "sha_release"
    assert commit_count == 2


def test_resolve_pr_base_ignores_branches_containing_the_pr():
    pr_ref = "refs/vimini/pr/1"
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = _fake_git(
            branches=("refs/remotes/origin/main", "refs/remotes/origin/topic"),
            merge_bases={
                # 'main' already contains the PR head, so it is not the base.
                "refs/remotes/origin/main": "sha_pr",
                "refs/remotes/origin/topic": "sha_topic",
            },
            commit_counts={
                f"sha_pr..{pr_ref}": 0,
                f"sha_topic..{pr_ref}": 1,
            },
        )
        base_ref, base_sha, commit_count = resolve_pr_base(
            "/repo", "origin", pr_ref, default_ref="refs/remotes/origin/main"
        )

    assert base_ref == "refs/remotes/origin/topic"
    assert base_sha == "sha_topic"
    assert commit_count == 1


def test_resolve_pr_base_prefers_default_branch_on_tie():
    pr_ref = "refs/vimini/pr/1"
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = _fake_git(
            branches=("refs/remotes/origin/devel", "refs/remotes/origin/main"),
            merge_bases={
                "refs/remotes/origin/devel": "sha_devel",
                "refs/remotes/origin/main": "sha_main",
            },
            commit_counts={
                f"sha_devel..{pr_ref}": 4,
                f"sha_main..{pr_ref}": 4,
            },
        )
        base_ref, _, commit_count = resolve_pr_base(
            "/repo", "origin", pr_ref, default_ref="refs/remotes/origin/main"
        )

    assert base_ref == "refs/remotes/origin/main"
    assert commit_count == 4


def test_resolve_pr_base_prefers_recency_order_over_name_length_on_tie():
    pr_ref = "refs/vimini/pr/1"
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = _fake_git(
            branches=(
                "refs/remotes/origin/release-1.0",
                "refs/remotes/origin/ci",
            ),
            merge_bases={
                "refs/remotes/origin/release-1.0": "sha_rel",
                "refs/remotes/origin/ci": "sha_ci",
            },
            commit_counts={
                f"sha_rel..{pr_ref}": 3,
                f"sha_ci..{pr_ref}": 3,
            },
        )
        base_ref, _, commit_count = resolve_pr_base(
            "/repo", "origin", pr_ref, default_ref="refs/remotes/origin/main"
        )

    assert base_ref == "refs/remotes/origin/release-1.0"
    assert commit_count == 3


def test_short_ref_strips_remote_prefixes():
    assert _short_ref("refs/remotes/origin/main", "origin") == "main"
    assert _short_ref("origin/main", "origin") == "main"
    assert _short_ref("main", "origin") == "main"


def test_get_remote_default_branch_uses_full_symbolic_ref():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = _fake_git(default_branch="refs/remotes/origin/main")
        assert (
            get_remote_default_branch("/repo", "origin") == "refs/remotes/origin/main"
        )
        cmds = _git_commands(mock_run)
        assert [
            "git",
            "-C",
            "/repo",
            "symbolic-ref",
            "refs/remotes/origin/HEAD",
        ] in cmds


def test_resolve_pr_base_returns_none_without_candidates():
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = _fake_git(branches=())
        assert resolve_pr_base("/repo", "origin", "refs/vimini/pr/1") == (
            None,
            None,
            None,
        )


def test_review_pr_warns_when_remote_refresh_fails(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("vimini.review.get_project_config", return_value=None),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.send_channel_request"),
        patch("vimini.util.new_split"),
        patch("vim.command"),
        patch("vimini.util.display_message") as mock_display,
    ):
        inner = _fake_git(commit_counts={"sha_base..refs/vimini/pr/5": 1})

        def fake_run(cmd, *args, **kwargs):
            gargs = list(cmd[3:]) if cmd[:2] == ["git", "-C"] else list(cmd)
            if gargs[0] == "fetch" and "--no-tags" in gargs:
                return MagicMock(returncode=1, stdout="", stderr="network unreachable")
            return inner(cmd, *args, **kwargs)

        mock_run.side_effect = fake_run

        review_module.review("Review", pr="5", remote="origin")

        warnings = [
            call[0][0]
            for call in mock_display.call_args_list
            if "could not refresh 'origin'" in call[0][0]
        ]
        assert len(warnings) == 1
        assert "network unreachable" in warnings[0]


def test_review_reports_resolved_base_and_commit_count(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("vimini.review.get_project_config", return_value=None),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.send_channel_request"),
        patch("vimini.util.new_split"),
        patch("vim.command"),
        patch("vimini.util.display_message") as mock_display,
    ):
        mock_run.side_effect = _fake_git(
            branches=("refs/remotes/origin/release-1", "refs/remotes/origin/main"),
            merge_bases={
                "refs/remotes/origin/release-1": "sha_release",
                "refs/remotes/origin/main": "sha_main",
            },
            commit_counts={
                "sha_release..refs/vimini/pr/7": 2,
                "sha_main..refs/vimini/pr/7": 8,
            },
        )

        review_module.review("Review", pr="7", remote="origin")

        messages = [call[0][0] for call in mock_display.call_args_list]
        assert any(
            "Reviewing 2 commit(s) of GitHub PR #7 (base: release-1)" in m
            for m in messages
        ), messages


# --- End-to-end regression tests against real git repositories ---


def _git(repo, *args):
    res = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=False
    )
    assert res.returncode == 0, (args, res.stderr)
    return res.stdout.strip()


def _commit(repo, name, message):
    (repo / name).write_text(message + "\n")
    _git(repo, "add", name)
    _git(repo, "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _show_commit_count(content):
    return sum(1 for line in content.splitlines() if line.startswith("commit "))


def _review_pr_capturing_params(local_repo, pr="1", **kwargs):
    """Runs review() against a real repository and returns the request params."""
    captured = {}

    with (
        patch("vimini.util.get_git_repo_root", return_value=str(local_repo)),
        patch("vimini.review.get_project_config", return_value=None),
        patch("vimini.util.get_git_repo_name", return_value="local"),
        patch(
            "vimini.util.send_channel_request",
            side_effect=lambda req: captured.update(req),
        ),
        patch("vimini.util.new_split"),
        patch("vimini.util.append_job_summary"),
        patch("vimini.util.append_to_buffer"),
        patch("vimini.util.reserve_next_job_id", return_value=1),
        patch("vimini.util.display_message"),
        patch("vim.command"),
    ):
        review_module.review("review this", pr=pr, remote="origin", **kwargs)

    assert "params" in captured, "review() did not submit a review request"
    return captured["params"]


def _build_upstream(tmp_path, pr_base="main", extra_main_commits=7):
    """
    Creates a bare upstream with a stale clone, a PR pushed to refs/pull/1/head,
    and returns the clone path. The clone's remote-tracking refs intentionally
    lag behind upstream.
    """
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(upstream)], check=True)

    work = tmp_path / "work"
    subprocess.run(
        ["git", "clone", str(upstream), str(work)], capture_output=True, check=True
    )
    _git(work, "config", "user.name", "T")
    _git(work, "config", "user.email", "t@example.com")

    for i in range(3):
        _commit(work, f"base{i}.txt", f"BASE {i}")
    _git(work, "push", "origin", "main")

    # The user clones here, so their remote-tracking refs stop at BASE 2.
    local = tmp_path / "local"
    subprocess.run(
        ["git", "clone", str(upstream), str(local)], capture_output=True, check=True
    )
    _git(local, "config", "user.name", "T")
    _git(local, "config", "user.email", "t@example.com")

    if pr_base != "main":
        _git(work, "checkout", "-b", pr_base)
        _commit(work, f"{pr_base}-1.txt", f"{pr_base.upper()} 1")
        _commit(work, f"{pr_base}-2.txt", f"{pr_base.upper()} 2")
        _git(work, "push", "origin", pr_base)
        _git(work, "checkout", "main")

    # Upstream moves on after the clone.
    for i in range(extra_main_commits):
        _commit(work, f"later{i}.txt", f"LATER {i}")
    _git(work, "push", "origin", "main")

    # The PR is opened on the current tip of its target branch.
    _git(work, "checkout", pr_base)
    _git(work, "checkout", "-b", "feature")
    _commit(work, "pr1.txt", "PR COMMIT 1")
    _commit(work, "pr2.txt", "PR COMMIT 2")
    _git(work, "push", "origin", "feature")
    _git(work, "push", "origin", "feature:refs/pull/1/head")
    _git(work, "checkout", "main")

    return local


def _build_empty_pr(tmp_path):
    """
    Creates an upstream and a clone where refs/pull/1/head points at the base
    branch tip, i.e. a PR that has already been merged or is empty.
    """
    upstream = tmp_path / "upstream.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(upstream)], check=True)

    work = tmp_path / "work"
    subprocess.run(
        ["git", "clone", str(upstream), str(work)], capture_output=True, check=True
    )
    _git(work, "config", "user.name", "T")
    _git(work, "config", "user.email", "t@example.com")

    for i in range(3):
        _commit(work, f"base{i}.txt", f"BASE {i}")
    _git(work, "push", "origin", "main")
    # The PR head is the base tip: nothing of its own to review.
    _git(work, "push", "origin", "main:refs/pull/1/head")

    local = tmp_path / "local"
    subprocess.run(
        ["git", "clone", str(upstream), str(local)], capture_output=True, check=True
    )
    return local


def test_review_pr_uses_fresh_base_branch_with_stale_clone(tmp_path):
    """
    A stale remote-tracking base branch must not pull unrelated upstream commits
    into the review (regression test).
    """
    local = _build_upstream(tmp_path)

    params = _review_pr_capturing_params(local)

    content = params["review_content"]
    assert _show_commit_count(content) == 2, content
    assert "PR COMMIT 1" in content
    assert "PR COMMIT 2" in content
    assert "LATER" not in content


def test_review_pr_uses_target_branch_not_default_branch(tmp_path):
    """
    A PR against a non-default branch must be diffed against that branch, not
    against the remote's default branch (regression test).
    """
    local = _build_upstream(tmp_path, pr_base="release-1")

    params = _review_pr_capturing_params(local)

    content = params["review_content"]
    assert _show_commit_count(content) == 2, content
    assert "PR COMMIT 1" in content
    assert "PR COMMIT 2" in content
    # Commits belonging to the target branch must not be reviewed.
    assert "RELEASE-1 1" not in content
    assert "RELEASE-1 2" not in content


def test_review_pr_batch_save_reviews_only_pr_commits(tmp_path):
    """
    Batch (--save) mode must derive its commit list from the same corrected
    range, so each saved review covers one PR commit only (regression test).
    """
    local = _build_upstream(tmp_path)

    params = _review_pr_capturing_params(local, save=True)

    commit_list = params["commit_list"]
    assert len(commit_list) == 2, commit_list

    subjects = [_git(local, "log", "-1", "--pretty=%s", sha) for sha in commit_list]
    assert subjects == ["PR COMMIT 1", "PR COMMIT 2"]


def test_review_empty_pr_submits_nothing(tmp_path):
    """
    An empty/already merged PR must not be submitted for review at all, so that
    no commit belonging to the base branch reaches the model (regression test).
    """
    local = _build_empty_pr(tmp_path)
    sent = []

    with (
        patch("vimini.util.get_git_repo_root", return_value=str(local)),
        patch("vimini.review.get_project_config", return_value=None),
        patch("vimini.util.get_git_repo_name", return_value="local"),
        patch("vimini.util.send_channel_request", side_effect=sent.append),
        patch("vimini.util.new_split") as mock_split,
        patch("vimini.util.append_job_summary"),
        patch("vimini.util.append_to_buffer"),
        patch("vimini.util.reserve_next_job_id", return_value=1) as mock_job_id,
        patch("vimini.util.display_message") as mock_display,
        patch("vim.command"),
    ):
        review_module.review("review this", pr="1", remote="origin")

    assert sent == []
    mock_job_id.assert_not_called()
    mock_split.assert_not_called()

    messages = [call[0][0] for call in mock_display.call_args_list]
    assert any("Nothing to review" in m and "GitHub PR #1" in m for m in messages), (
        messages
    )


def test_review_empty_pr_in_batch_mode_submits_nothing(tmp_path):
    """
    Batch (--save) mode must stop for an empty/already merged PR too, instead of
    walking the base branch tip with --no-walk (regression test).
    """
    local = _build_empty_pr(tmp_path)
    sent = []

    with (
        patch("vimini.util.get_git_repo_root", return_value=str(local)),
        patch("vimini.review.get_project_config", return_value=None),
        patch("vimini.util.get_git_repo_name", return_value="local"),
        patch("vimini.util.send_channel_request", side_effect=sent.append),
        patch("vimini.util.reserve_next_job_id", return_value=1) as mock_job_id,
        patch("vimini.util.display_message"),
    ):
        review_module.review(
            "review this", pr="1", remote="origin", save=True, save_path=str(tmp_path)
        )

    assert sent == []
    mock_job_id.assert_not_called()
