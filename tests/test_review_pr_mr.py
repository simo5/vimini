import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# Ensure python3 root is in sys.path
sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

import vimini.review as review_module


def test_review_github_pr_success(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
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

        # Verify git fetch call
        fetch_call = mock_run.call_args_list[0]
        assert fetch_call[0][0] == [
            "git",
            "-C",
            repo_path,
            "fetch",
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


def test_review_gitlab_mr_success(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
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


def test_review_pr_invalid_number():
    with (
        patch("vimini.util.display_message") as mock_display,
        patch("subprocess.run") as mock_run,
    ):
        review_module.review("test", pr="123;rm -rf")
        mock_display.assert_called_once()
        assert "numeric" in mock_display.call_args[0][0]
        mock_run.assert_not_called()


def test_review_pr_invalid_remote():
    with (
        patch("vimini.util.display_message") as mock_display,
        patch("subprocess.run") as mock_run,
    ):
        review_module.review("test", pr="123", remote="-oProxyCommand=calc")
        mock_display.assert_called_once()
        assert "Invalid remote name" in mock_display.call_args[0][0]
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
