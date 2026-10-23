import sys
import os
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini.code import apply_patch


def test_apply_patch_empty_diff():
    success, err = apply_patch("", silent=True)
    assert success is False
    assert "empty" in err


def test_apply_patch_with_patch_command_success(tmp_path):
    project_root = str(tmp_path)
    diff_content = "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-a\n+b\n"

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        success, err = apply_patch(diff_content, project_root=project_root, silent=True)

        assert success is True
        assert err is None
        patch_calls = [c for c in mock_run.call_args_list if c[0][0][0] == "patch"]
        assert len(patch_calls) == 1
        assert patch_calls[0][0][0] == ["patch", "-p1", "-N", "-r", "-"]


def test_apply_patch_fallback_to_git_apply(tmp_path):
    project_root = str(tmp_path)
    diff_content = "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-a\n+b\n"

    with patch("subprocess.run") as mock_run:
        # First call (patch) raises FileNotFoundError, second call (git apply) succeeds
        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == "patch":
                raise FileNotFoundError("patch not found")
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run

        success, err = apply_patch(diff_content, project_root=project_root, silent=True)

        assert success is True
        assert err is None
        git_calls = [c for c in mock_run.call_args_list if "apply" in c[0][0]]
        assert len(git_calls) == 1
        git_cmd = git_calls[0][0][0]
        assert git_cmd == [
            "git",
            "-C",
            project_root,
            "apply",
            "--whitespace=nowarn",
            "-",
        ]


def test_apply_patch_fallback_git_apply_failure(tmp_path):
    project_root = str(tmp_path)
    diff_content = "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-a\n+b\n"

    with patch("subprocess.run") as mock_run:

        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == "patch":
                raise FileNotFoundError("patch not found")
            return MagicMock(
                returncode=1, stdout="", stderr="error: patch does not apply"
            )

        mock_run.side_effect = fake_run

        success, err = apply_patch(diff_content, project_root=project_root, silent=True)

        assert success is False
        assert "error: patch does not apply" in err


def test_apply_patch_both_patch_and_git_missing(tmp_path):
    project_root = str(tmp_path)
    diff_content = "diff --git a/file.py b/file.py\n--- a/file.py\n+++ b/file.py\n@@ -1 +1 @@\n-a\n+b\n"

    with patch("subprocess.run") as mock_run:

        def fake_run(cmd, *args, **kwargs):
            raise FileNotFoundError("command not found")

        mock_run.side_effect = fake_run

        success, err = apply_patch(diff_content, project_root=project_root, silent=True)

        assert success is False
        assert "commands not available" in err
