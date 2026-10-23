import sys
import os
import json
import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini.commit import (
    parse_file_line,
    toggle_file,
    handle_commit_response,
    finalize_commit,
    _revert_staged,
    _commit_files_by_file,
)


def test_parse_file_line():
    commit_files = ["src/app.py", "README.md", "path/with space/file.txt"]

    # Included file
    fn, deselected = parse_file_line("#   src/app.py", commit_files)
    assert fn == "src/app.py"
    assert deselected is False

    # Deselected file
    fn, deselected = parse_file_line("# - src/app.py", commit_files)
    assert fn == "src/app.py"
    assert deselected is True

    # Variations
    fn, deselected = parse_file_line("# -src/app.py", commit_files)
    assert fn == "src/app.py"
    assert deselected is True

    fn, deselected = parse_file_line("#- src/app.py", commit_files)
    assert fn == "src/app.py"
    assert deselected is True

    fn, deselected = parse_file_line("# src/app.py", commit_files)
    assert fn == "src/app.py"
    assert deselected is False

    # Path with space
    fn, deselected = parse_file_line("#   path/with space/file.txt", commit_files)
    assert fn == "path/with space/file.txt"
    assert deselected is False

    fn, deselected = parse_file_line("# - path/with space/file.txt", commit_files)
    assert fn == "path/with space/file.txt"
    assert deselected is True

    # Non-file lines should return None, None
    assert parse_file_line("# --- Files to commit ---", commit_files) == (None, None)
    assert parse_file_line(
        "# (Press <Space> to toggle file inclusion)", commit_files
    ) == (None, None)
    assert parse_file_line("#  src/app.py | 5 ++---", commit_files) == (None, None)
    assert parse_file_line("# This is a normal comment", commit_files) == (None, None)
    assert parse_file_line("Commit subject line", commit_files) == (None, None)
    assert parse_file_line("", commit_files) == (None, None)


def test_toggle_file():
    import vim

    commit_files = ["src/app.py", "README.md"]
    mock_buffer = [
        "Commit title",
        "",
        "# --- Files to commit ---",
        "#   src/app.py",
        "# - README.md",
    ]

    with (
        patch.object(vim, "eval") as mock_eval,
        patch.object(vim, "command") as mock_cmd,
    ):
        vim.current.buffer = mock_buffer
        vim.current.window.cursor = (4, 4)  # Row 4 is "#   src/app.py"

        mock_eval.side_effect = lambda expr: (
            json.dumps(commit_files) if "vimini_commit_files" in expr else ""
        )

        # Toggle src/app.py (included -> deselected)
        toggle_file()
        assert vim.current.buffer[3] == "# - src/app.py"

        # Toggle src/app.py again (deselected -> included)
        toggle_file()
        assert vim.current.buffer[3] == "#   src/app.py"

        # Move to row 5 ("# - README.md") and toggle (deselected -> included)
        vim.current.window.cursor = (5, 4)
        toggle_file()
        assert vim.current.buffer[4] == "#   README.md"

        # Move to non-file line (Row 3: header) and toggle -> should not modify line
        vim.current.window.cursor = (3, 2)
        toggle_file()
        assert vim.current.buffer[2] == "# --- Files to commit ---"


def test_handle_commit_response_adds_files_and_mapping(tmp_path):
    import vim

    result = {
        "text": "Add user authentication\n---\nImplements OAuth2 login.",
        "repo_path": "/dummy/repo",
        "diff_stat_output": " src/app.py | 2 +-\n 1 file changed",
        "commit_files": ["src/app.py"],
        "regenerate": False,
        "assistant": True,
    }

    mock_buffer = []
    vim.current.buffer = mock_buffer

    with (
        patch("vimini.util.new_split") as mock_split,
        patch.object(vim, "command") as mock_cmd,
        patch("vimini.util.display_message") as mock_display,
    ):
        handle_commit_response("job_1", result)

        assert mock_split.called
        # Check buffer lines
        assert "Add user authentication" in vim.current.buffer
        assert "Implements OAuth2 login." in vim.current.buffer
        assert "# --- Diff stat ---" in vim.current.buffer
        assert "# --- Files to commit ---" in vim.current.buffer
        assert "# (Press <Space> to toggle file inclusion)" in vim.current.buffer
        assert "#   src/app.py" in vim.current.buffer

        cmd_calls = [c[0][0] for c in mock_cmd.call_args_list]
        assert any("b:vimini_commit_files" in c for c in cmd_calls)
        assert any("ViminiCommitToggleFile" in c for c in cmd_calls)


def test_finalize_commit_removes_deselected_files(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg.txt"
    repo_path = str(tmp_path)
    commit_files = ["src/included.py", "src/excluded.py"]

    content = (
        "Commit subject\n\n"
        "Commit body\n\n"
        "# --- Files to commit ---\n"
        "#   src/included.py\n"
        "# - src/excluded.py\n"
    )
    tmp_file.write_text(content, encoding="utf-8")

    def mock_eval_fn(expr):
        if "vimini_commit_tmp_file" in expr:
            return str(tmp_file)
        if "vimini_commit_repo_path" in expr:
            return repo_path
        if "vimini_commit_regenerate" in expr:
            return 0
        if "vimini_commit_assistant" in expr:
            return 1
        if "vimini_commit_files" in expr:
            return json.dumps(commit_files)
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_eval_fn),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.display_message") as mock_display,
    ):
        mock_proc_ok = MagicMock(
            returncode=0, stdout="[main abc1234] Commit subject", stderr=""
        )
        mock_run.return_value = mock_proc_ok

        finalize_commit()

        # Check subprocess calls
        run_cmds = [c[0][0] for c in mock_run.call_args_list]

        # Verify unstage was called for excluded.py
        unstage_calls = [
            cmd for cmd in run_cmds if "reset" in cmd and "src/excluded.py" in cmd
        ]
        assert len(unstage_calls) == 1
        assert "src/included.py" not in unstage_calls[0]

        # Verify commit command was called
        commit_calls = [cmd for cmd in run_cmds if "commit" in cmd and "-s" in cmd]
        assert len(commit_calls) == 1


def test_finalize_commit_all_files_deselected_cancels(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_all_deselected.txt"
    repo_path = str(tmp_path)
    commit_files = ["file1.py", "file2.py"]

    content = (
        "Commit subject\n\n# --- Files to commit ---\n# - file1.py\n# - file2.py\n"
    )
    tmp_file.write_text(content, encoding="utf-8")

    def mock_eval_fn(expr):
        if "vimini_commit_tmp_file" in expr:
            return str(tmp_file)
        if "vimini_commit_repo_path" in expr:
            return repo_path
        if "vimini_commit_regenerate" in expr:
            return 0
        if "vimini_commit_assistant" in expr:
            return 0
        if "vimini_commit_files" in expr:
            return json.dumps(commit_files)
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_eval_fn),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.display_message") as mock_display,
    ):
        mock_proc_ok = MagicMock(returncode=0, stdout="", stderr="")
        mock_run.return_value = mock_proc_ok

        finalize_commit()

        # Should display cancellation error
        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any("no files selected to commit" in str(c) for c in err_calls)

        # git commit should NOT be called
        run_cmds = [c[0][0] for c in mock_run.call_args_list]
        commit_calls = [cmd for cmd in run_cmds if "commit" in cmd and "-s" in cmd]
        assert len(commit_calls) == 0


def test_finalize_commit_regenerate_uses_head_parent(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_regen.txt"
    repo_path = str(tmp_path)
    commit_files = ["fileA.py", "fileB.py"]

    content = (
        "Amended subject\n\n# --- Files in commit ---\n#   fileA.py\n# - fileB.py\n"
    )
    tmp_file.write_text(content, encoding="utf-8")

    def mock_eval_fn(expr):
        if "vimini_commit_tmp_file" in expr:
            return str(tmp_file)
        if "vimini_commit_repo_path" in expr:
            return repo_path
        if "vimini_commit_regenerate" in expr:
            return 1
        if "vimini_commit_assistant" in expr:
            return 0
        if "vimini_commit_files" in expr:
            return json.dumps(commit_files)
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_eval_fn),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.display_message") as mock_display,
    ):
        mock_proc_ok = MagicMock(returncode=0, stdout="", stderr="")
        mock_run.return_value = mock_proc_ok

        finalize_commit()

        run_cmds = [c[0][0] for c in mock_run.call_args_list]

        # Verify unstage in regenerate mode resets against HEAD~1
        unstage_calls = [
            cmd
            for cmd in run_cmds
            if "reset" in cmd and "HEAD~1" in cmd and "fileB.py" in cmd
        ]
        assert len(unstage_calls) == 1

        # Verify commit command includes --amend
        commit_calls = [cmd for cmd in run_cmds if "commit" in cmd and "--amend" in cmd]
        assert len(commit_calls) == 1
