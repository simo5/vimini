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
from vimini.review import resolve_remote
from vimini.common.util import temporary_git_worktree, resolve_worktree_dir


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
        # Configure mock subprocess returns:
        # 1: git fetch -> success
        # 2: git symbolic-ref -> "origin/main"
        # 3: git merge-base -> "sha_base"
        # 4: git rev-parse -> "sha_pr"
        # 5: git show -> "diff_content"
        mock_fetch = MagicMock(returncode=0, stdout="", stderr="")
        mock_sym = MagicMock(returncode=0, stdout="origin/main\n", stderr="")
        mock_mb = MagicMock(returncode=0, stdout="sha_base\n", stderr="")
        mock_rev = MagicMock(returncode=0, stdout="sha_pr\n", stderr="")
        mock_show = MagicMock(returncode=0, stdout="diff_content\n", stderr="")

        mock_run.side_effect = [mock_fetch, mock_sym, mock_mb, mock_rev, mock_show]

        review_module.review("Review this PR", pr="123", remote="origin")

        # Verify git fetch call with '--' before positional arguments
        fetch_call = mock_run.call_args_list[0]
        assert fetch_call[0][0] == [
            "git",
            "-C",
            repo_path,
            "fetch",
            "--",
            "origin",
            "+refs/pull/123/head:refs/vimini/pr/123",
        ]

        # Verify merge-base call uses origin/main
        mb_call = mock_run.call_args_list[2]
        assert mb_call[0][0] == [
            "git",
            "-C",
            repo_path,
            "merge-base",
            "origin/main",
            "refs/vimini/pr/123",
        ]

        # Verify git show call
        show_call = mock_run.call_args_list[4]
        assert show_call[0][0] == [
            "git",
            "-C",
            repo_path,
            "show",
            "sha_base..refs/vimini/pr/123",
        ]

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
        mock_fetch = MagicMock(returncode=0, stdout="", stderr="")
        mock_sym = MagicMock(returncode=1, stdout="", stderr="")
        mock_chk_main = MagicMock(returncode=0, stdout="sha_main\n", stderr="")
        mock_mb = MagicMock(returncode=0, stdout="sha_base\n", stderr="")
        mock_rev = MagicMock(returncode=0, stdout="sha_mr\n", stderr="")
        mock_show = MagicMock(returncode=0, stdout="diff_content\n", stderr="")

        mock_run.side_effect = [
            mock_fetch,
            mock_sym,
            mock_chk_main,
            mock_mb,
            mock_rev,
            mock_show,
        ]

        review_module.review("Review MR", mr="456", remote="upstream", git_objects=None)

        fetch_call = mock_run.call_args_list[0]
        assert fetch_call[0][0] == [
            "git",
            "-C",
            repo_path,
            "fetch",
            "--",
            "upstream",
            "+refs/merge-requests/456/head:refs/vimini/mr/456",
        ]

        mb_call = mock_run.call_args_list[3]
        assert mb_call[0][0] == [
            "git",
            "-C",
            repo_path,
            "merge-base",
            "upstream/main",
            "refs/vimini/mr/456",
        ]
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
        mock_fetch = MagicMock(returncode=0, stdout="", stderr="")
        mock_sym = MagicMock(returncode=0, stdout="origin/main\n", stderr="")
        mock_mb = MagicMock(returncode=0, stdout="sha_base\n", stderr="")
        mock_rev = MagicMock(returncode=0, stdout="sha_pr\n", stderr="")
        mock_show = MagicMock(returncode=0, stdout="diff_content\n", stderr="")

        mock_run.side_effect = [mock_fetch, mock_sym, mock_mb, mock_rev, mock_show]

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
        mock_fetch = MagicMock(returncode=0, stdout="", stderr="")
        mock_sym = MagicMock(returncode=0, stdout="origin/main\n", stderr="")
        mock_mb = MagicMock(returncode=0, stdout="sha_base\n", stderr="")
        mock_rev = MagicMock(returncode=0, stdout="sha_pr\n", stderr="")
        # Simulate stdout with replaced non-utf8 characters (\ufffd)
        mock_show = MagicMock(
            returncode=0, stdout="diff with binary byte: \ufffd\n", stderr=""
        )

        mock_run.side_effect = [mock_fetch, mock_sym, mock_mb, mock_rev, mock_show]

        review_module.review("Review non-utf8 PR", pr="123", remote="origin")

        # Verify encoding and errors parameters in git show and fetch calls
        fetch_call = mock_run.call_args_list[0]
        assert fetch_call.kwargs.get("encoding") == "utf-8"
        assert fetch_call.kwargs.get("errors") == "replace"

        show_call = mock_run.call_args_list[4]
        assert show_call.kwargs.get("encoding") == "utf-8"
        assert show_call.kwargs.get("errors") == "replace"

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
        mock_fetch = MagicMock(returncode=0, stdout="", stderr="")
        mock_sym = MagicMock(returncode=0, stdout="origin/main\n", stderr="")
        mock_mb = MagicMock(returncode=0, stdout="sha_base\n", stderr="")
        mock_rev = MagicMock(returncode=0, stdout="sha_pr\n", stderr="")
        mock_show = MagicMock(returncode=0, stdout="diff_content\n", stderr="")

        mock_run.side_effect = [mock_fetch, mock_sym, mock_mb, mock_rev, mock_show]

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
        # 1: git remote -> "gh\n"
        mock_remote = MagicMock(returncode=0, stdout="gh\n", stderr="")
        # 2: git symbolic-ref -> "main"
        mock_branch = MagicMock(returncode=0, stdout="main\n", stderr="")
        # 3: git config branch.main.remote -> "gh"
        mock_cfg = MagicMock(returncode=0, stdout="gh\n", stderr="")
        # 4: git fetch -- gh ... -> success
        mock_fetch = MagicMock(returncode=0, stdout="", stderr="")
        # 5: git symbolic-ref refs/remotes/gh/HEAD -> "gh/main"
        mock_sym = MagicMock(returncode=0, stdout="gh/main\n", stderr="")
        # 6: git merge-base -> "sha_base"
        mock_mb = MagicMock(returncode=0, stdout="sha_base\n", stderr="")
        # 7: git rev-parse -> "sha_pr"
        mock_rev = MagicMock(returncode=0, stdout="sha_pr\n", stderr="")
        # 8: git show -> "diff_content"
        mock_show = MagicMock(returncode=0, stdout="diff_content\n", stderr="")

        mock_run.side_effect = [
            mock_remote,
            mock_branch,
            mock_cfg,
            mock_fetch,
            mock_sym,
            mock_mb,
            mock_rev,
            mock_show,
        ]

        # Call review without remote argument
        review_module.review("Review PR auto-detect", pr="789")

        # Verify fetch call was made with '--' and 'gh'
        fetch_call = mock_run.call_args_list[3]
        assert fetch_call[0][0] == [
            "git",
            "-C",
            repo_path,
            "fetch",
            "--",
            "gh",
            "+refs/pull/789/head:refs/vimini/pr/789",
        ]
        mock_send.assert_called_once()
