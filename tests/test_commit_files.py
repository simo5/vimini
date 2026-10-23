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
    _extract_rename_target,
    handle_commit_response,
    commit,
    finalize_commit,
    _revert_staged,
    _revert_amend,
    _commit_files_by_file,
)


def test_extract_rename_target():
    assert _extract_rename_target("{old => new}/file.txt") == "new/file.txt"
    assert _extract_rename_target("{dir => }/file.txt") == "file.txt"
    assert _extract_rename_target("{ => dir}/file.txt") == "dir/file.txt"
    assert _extract_rename_target("dir/{old => new}.py") == "dir/new.py"
    assert _extract_rename_target("old.txt => new.txt") == "new.txt"
    assert _extract_rename_target("no rename here") is None


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

    # Diff stat formatted lines
    fn, deselected = parse_file_line("#   src/app.py | 5 ++---", commit_files)
    assert fn == "src/app.py"
    assert deselected is False

    fn, deselected = parse_file_line("# - src/app.py | 5 ++---", commit_files)
    assert fn == "src/app.py"
    assert deselected is True

    fn, deselected = parse_file_line(
        "#   path/with space/file.txt | 2 +-", commit_files
    )
    assert fn == "path/with space/file.txt"
    assert deselected is False

    # Renamed files in diff stat
    commit_files_rename = ["file.txt", "new_dir/sub.py", "app.py"]
    fn, deselected = parse_file_line(
        "#   {old_dir => }/file.txt | 2 +-", commit_files_rename
    )
    assert fn == "file.txt"
    assert deselected is False

    fn, deselected = parse_file_line(
        "# - {old_dir => }/file.txt | 2 +-", commit_files_rename
    )
    assert fn == "file.txt"
    assert deselected is True

    fn, deselected = parse_file_line(
        "#   old_app.py => app.py | 5 ++---", commit_files_rename
    )
    assert fn == "app.py"
    assert deselected is False

    # Files with leading hyphens
    commit_files_hyphens = ["-file.txt", "--flag.py"]
    fn, deselected = parse_file_line("#   -file.txt", commit_files_hyphens)
    assert fn == "-file.txt"
    assert deselected is False

    fn, deselected = parse_file_line("#   -file.txt | 2 +-", commit_files_hyphens)
    assert fn == "-file.txt"
    assert deselected is False

    fn, deselected = parse_file_line("# - -file.txt", commit_files_hyphens)
    assert fn == "-file.txt"
    assert deselected is True

    fn, deselected = parse_file_line("# - -file.txt | 2 +-", commit_files_hyphens)
    assert fn == "-file.txt"
    assert deselected is True

    fn, deselected = parse_file_line("#   --flag.py", commit_files_hyphens)
    assert fn == "--flag.py"
    assert deselected is False

    # Path with pipe character in diff stat
    commit_files_pipe = ["path/to|file.txt"]
    fn, deselected = parse_file_line("#   path/to|file.txt | 2 +-", commit_files_pipe)
    assert fn == "path/to|file.txt"
    assert deselected is False

    fn, deselected = parse_file_line("# - path/to|file.txt | 2 +-", commit_files_pipe)
    assert fn == "path/to|file.txt"
    assert deselected is True

    # Non-file lines should return None, None
    assert parse_file_line("# --- Diff stat ---", commit_files) == (None, None)
    assert parse_file_line(
        "# (Press <Space> to toggle file inclusion)", commit_files
    ) == (None, None)
    assert parse_file_line(
        "#  2 files changed, 5 insertions(+), 2 deletions(-)", commit_files
    ) == (None, None)
    assert parse_file_line("# This is a normal comment", commit_files) == (None, None)
    assert parse_file_line("Commit subject line", commit_files) == (None, None)
    assert parse_file_line("", commit_files) == (None, None)


def test_toggle_file():
    import vim

    commit_files = ["src/app.py", "README.md"]
    mock_buffer = [
        "Commit title",
        "",
        "# --- Diff stat ---",
        "# (Press <Space> to toggle file inclusion)",
        "#   src/app.py | 2 +-",
        "# - README.md  | 5 +++--",
        "#  2 files changed",
    ]

    with (
        patch.object(vim.current, "buffer", mock_buffer),
        patch.object(vim, "eval") as mock_eval,
        patch.object(vim, "command") as mock_cmd,
    ):
        vim.current.window.cursor = (5, 4)  # Row 5 is "#   src/app.py | 2 +-"

        mock_eval.side_effect = lambda expr: (
            json.dumps(commit_files) if "vimini_commit_files" in expr else ""
        )

        # Toggle src/app.py (included -> deselected, preserving stat)
        toggle_file()
        assert vim.current.buffer[4] == "# - src/app.py | 2 +-"

        # Toggle src/app.py again (deselected -> included, preserving stat)
        toggle_file()
        assert vim.current.buffer[4] == "#   src/app.py | 2 +-"

        # Move to row 6 ("# - README.md  | 5 +++--") and toggle (deselected -> included)
        vim.current.window.cursor = (6, 4)
        toggle_file()
        assert vim.current.buffer[5] == "#   README.md  | 5 +++--"

        # Move to non-file line (Row 3: header) -> should not modify line
        vim.current.window.cursor = (3, 2)
        toggle_file()
        assert vim.current.buffer[2] == "# --- Diff stat ---"

        # Move to summary line (Row 7) -> should not modify line
        vim.current.window.cursor = (7, 2)
        toggle_file()
        assert vim.current.buffer[6] == "#  2 files changed"


def test_toggle_file_with_leading_hyphen():
    import vim

    commit_files = ["-file.txt", "--flag.py"]
    mock_buffer = [
        "Commit title",
        "",
        "# --- Diff stat ---",
        "# (Press <Space> to toggle file inclusion)",
        "#   -file.txt | 2 +-",
        "#   --flag.py | 1 +",
    ]

    with (
        patch.object(vim.current, "buffer", mock_buffer),
        patch.object(vim, "eval") as mock_eval,
        patch.object(vim, "command") as mock_cmd,
    ):
        vim.current.window.cursor = (5, 4)

        mock_eval.side_effect = lambda expr: (
            json.dumps(commit_files) if "vimini_commit_files" in expr else ""
        )

        toggle_file()
        assert vim.current.buffer[4] == "# - -file.txt | 2 +-"
        toggle_file()
        assert vim.current.buffer[4] == "#   -file.txt | 2 +-"

        vim.current.window.cursor = (6, 4)
        toggle_file()
        assert vim.current.buffer[5] == "# - --flag.py | 1 +"
        toggle_file()
        assert vim.current.buffer[5] == "#   --flag.py | 1 +"


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

    fake_temp = tmp_path / "fake.gitcommit"
    fake_temp.touch()
    mock_buffer = []
    with (
        patch.object(vim.current, "buffer", mock_buffer),
        patch("vimini.util.new_split") as mock_split,
        patch.object(vim, "command") as mock_cmd,
        patch("vimini.util.display_message") as mock_display,
        patch("tempfile.NamedTemporaryFile") as mock_named_temp,
    ):
        mock_named_temp.return_value.__enter__.return_value.name = str(fake_temp)
        try:
            handle_commit_response("job_1", result)

            assert mock_split.called
            # Check buffer lines
            assert "Add user authentication" in vim.current.buffer
            assert "Implements OAuth2 login." in vim.current.buffer
            assert "# --- Diff stat ---" in vim.current.buffer
            assert "# (Press <Space> to toggle file inclusion)" in vim.current.buffer
            assert "#   src/app.py | 2 +-" in vim.current.buffer
            assert "#  1 file changed" in vim.current.buffer
            assert "# --- Files to commit ---" not in vim.current.buffer
        finally:
            _commit_files_by_file.pop(str(fake_temp), None)
            if fake_temp.exists():
                fake_temp.unlink()
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
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "#   src/included.py | 2 +-\n"
        "# - src/excluded.py | 3 +--\n"
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
        "Commit subject\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "# - file1.py | 1 +\n"
        "# - file2.py | 1 -\n"
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
        "Amended subject\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "#   fileA.py | 1 +\n"
        "# - fileB.py | 1 -\n"
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


def test_finalize_commit_unstage_failure_reverts_staged(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_fail.txt"
    repo_path = str(tmp_path)
    commit_files = ["src/included.py", "src/excluded.py"]

    content = (
        "Commit subject\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "#   src/included.py | 1 +\n"
        "# - src/excluded.py | 1 -\n"
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
        patch("vimini.commit._revert_staged") as mock_revert,
        patch("vimini.util.display_message") as mock_display,
    ):

        def fake_run(cmd, *args, **kwargs):
            if "reset" in cmd and "src/excluded.py" in cmd:
                return MagicMock(
                    returncode=1, stderr="fatal error unstaging", stdout=""
                )
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run

        finalize_commit()

        # Should display error message
        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any("Failed to unstage deselected files" in str(c) for c in err_calls)

        # Must revert staged changes on failure
        assert mock_revert.called
        mock_revert.assert_called_once_with(repo_path)


def test_finalize_commit_unstage_exception_reverts_staged(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_exc.txt"
    repo_path = str(tmp_path)
    commit_files = ["src/included.py", "src/excluded.py"]

    content = (
        "Commit subject\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "#   src/included.py | 1 +\n"
        "# - src/excluded.py | 1 -\n"
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
        patch("vimini.commit._revert_staged") as mock_revert,
        patch("vimini.util.display_message") as mock_display,
    ):
        mock_run.side_effect = OSError("git process failed")

        finalize_commit()

        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any("Error unstaging deselected files" in str(c) for c in err_calls)
        assert mock_revert.called
        mock_revert.assert_called_once_with(repo_path)


def test_finalize_commit_deleted_file_lines_deselected(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_deleted_line.txt"
    repo_path = str(tmp_path)
    commit_files = ["src/kept.py", "src/deleted.py"]

    # User deleted "#   src/deleted.py | 1 -" line completely
    content = (
        "Commit subject\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "#   src/kept.py | 1 +\n"
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
        mock_proc_ok = MagicMock(
            returncode=0, stdout="[main abc1234] Commit subject", stderr=""
        )
        mock_run.return_value = mock_proc_ok

        finalize_commit()

        run_cmds = [c[0][0] for c in mock_run.call_args_list]

        # Deleted file should be unstaged via reset
        unstage_calls = [
            cmd for cmd in run_cmds if "reset" in cmd and "src/deleted.py" in cmd
        ]
        assert len(unstage_calls) == 1
        assert "src/kept.py" not in unstage_calls[0]

        # Kept file should proceed to commit
        commit_calls = [cmd for cmd in run_cmds if "commit" in cmd and "-s" in cmd]
        assert len(commit_calls) == 1


def test_revert_amend_invokes_git_reset(tmp_path):
    repo_path = str(tmp_path)
    orig_head = "deadbeef1234"

    with patch("subprocess.run") as mock_run:
        _revert_amend(repo_path, orig_head)
        assert mock_run.called
        cmd = mock_run.call_args[0][0]
        assert cmd == ["git", "-C", repo_path, "reset", orig_head]


def test_finalize_commit_amend_aborted_by_quitting_reverts_amend(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_empty.txt"
    # Empty content simulates closing the buffer without saving
    tmp_file.write_text("", encoding="utf-8")
    repo_path = str(tmp_path)
    orig_head = "orig_commit_sha_123"

    def mock_eval_fn(expr):
        if "vimini_commit_tmp_file" in expr:
            return str(tmp_file)
        if "vimini_commit_repo_path" in expr:
            return repo_path
        if "vimini_commit_regenerate" in expr:
            return 1
        if "vimini_commit_amend" in expr:
            return 1
        if "vimini_commit_orig_head" in expr:
            return orig_head
        if "vimini_commit_assistant" in expr:
            return 0
        if "vimini_commit_files" in expr:
            return json.dumps(["file1.py"])
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_eval_fn),
        patch("vimini.commit._revert_amend") as mock_revert_amend,
        patch("vimini.util.display_message") as mock_display,
    ):
        finalize_commit()

        # Must call _revert_amend with repo_path and orig_head
        assert mock_revert_amend.called
        mock_revert_amend.assert_called_once_with(repo_path, orig_head)

        # Should display amend cancellation with revert note
        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any(
            "Amend cancelled" in str(c) and "Reverting amend" in str(c)
            for c in err_calls
        )


def test_finalize_commit_amend_all_files_deselected_reverts_amend(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_amend_deselected.txt"
    repo_path = str(tmp_path)
    orig_head = "orig_commit_sha_456"
    commit_files = ["f1.py", "f2.py"]

    content = (
        "Amend subject\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "# - f1.py | 1 +\n"
        "# - f2.py | 1 -\n"
    )
    tmp_file.write_text(content, encoding="utf-8")

    def mock_eval_fn(expr):
        if "vimini_commit_tmp_file" in expr:
            return str(tmp_file)
        if "vimini_commit_repo_path" in expr:
            return repo_path
        if "vimini_commit_regenerate" in expr:
            return 1
        if "vimini_commit_amend" in expr:
            return 1
        if "vimini_commit_orig_head" in expr:
            return orig_head
        if "vimini_commit_assistant" in expr:
            return 0
        if "vimini_commit_files" in expr:
            return json.dumps(commit_files)
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_eval_fn),
        patch("vimini.commit._revert_amend") as mock_revert_amend,
        patch("vimini.util.display_message") as mock_display,
    ):
        finalize_commit()

        assert mock_revert_amend.called
        mock_revert_amend.assert_called_once_with(repo_path, orig_head)

        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any(
            "Amend cancelled" in str(c) and "Reverting amend" in str(c)
            for c in err_calls
        )


def test_finalize_commit_amend_commit_failure_reverts_amend(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_amend_fail.txt"
    repo_path = str(tmp_path)
    orig_head = "orig_commit_sha_789"
    commit_files = ["f1.py"]

    content = (
        "Amend subject\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "#   f1.py | 1 +\n"
    )
    tmp_file.write_text(content, encoding="utf-8")

    def mock_eval_fn(expr):
        if "vimini_commit_tmp_file" in expr:
            return str(tmp_file)
        if "vimini_commit_repo_path" in expr:
            return repo_path
        if "vimini_commit_regenerate" in expr:
            return 1
        if "vimini_commit_amend" in expr:
            return 1
        if "vimini_commit_orig_head" in expr:
            return orig_head
        if "vimini_commit_assistant" in expr:
            return 0
        if "vimini_commit_files" in expr:
            return json.dumps(commit_files)
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_eval_fn),
        patch("subprocess.run") as mock_run,
        patch("vimini.commit._revert_amend") as mock_revert_amend,
        patch("vimini.util.display_message") as mock_display,
    ):
        # Simulate final git commit failing (e.g. hook rejected)
        mock_run.return_value = MagicMock(
            returncode=1, stderr="pre-commit hook failed", stdout=""
        )

        finalize_commit()

        assert mock_revert_amend.called
        mock_revert_amend.assert_called_once_with(repo_path, orig_head)

        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any("Git amend failed" in str(c) for c in err_calls)


def test_finalize_commit_regenerate_commit_failure_resets_index(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_regen_fail.txt"
    repo_path = str(tmp_path)
    commit_files = ["f1.py", "f2.py"]

    content = (
        "Regen subject\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "#   f1.py | 1 +\n"
        "# - f2.py | 1 -\n"
    )
    tmp_file.write_text(content, encoding="utf-8")

    def mock_eval_fn(expr):
        if "vimini_commit_tmp_file" in expr:
            return str(tmp_file)
        if "vimini_commit_repo_path" in expr:
            return repo_path
        if "vimini_commit_regenerate" in expr:
            return 1
        if "vimini_commit_amend" in expr:
            return 0
        if "vimini_commit_orig_head" in expr:
            return ""
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
        mock_run.side_effect = lambda cmd, *a, **kw: (
            MagicMock(returncode=1, stderr="pre-commit hook failed", stdout="")
            if "commit" in cmd
            else MagicMock(returncode=0, stdout="", stderr="")
        )

        finalize_commit()

        run_cmds = [c[0][0] for c in mock_run.call_args_list]
        assert ["git", "-C", repo_path, "reset", "HEAD", "--"] in run_cmds
        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any("Git amend failed" in str(c) for c in err_calls)


def test_commit_stat_commands_include_stat_name_width(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("vimini.commit._run_format_command", return_value=True),
        patch("vimini.commit._stage_changes", return_value=True),
        patch("vimini.util.send_channel_request", return_value=True),
        patch("subprocess.run") as mock_run,
    ):
        mock_proc_diff = MagicMock(returncode=0, stdout="diff --git a/f b/f", stderr="")
        mock_proc_stat = MagicMock(returncode=0, stdout="f | 1 +", stderr="")
        mock_proc_files = MagicMock(returncode=0, stdout="f\0", stderr="")

        def fake_run(cmd, *args, **kwargs):
            if "--stat" in cmd:
                return mock_proc_stat
            if "--name-only" in cmd:
                return mock_proc_files
            return mock_proc_diff

        mock_run.side_effect = fake_run

        commit(regenerate=False)
        stat_calls = [c[0][0] for c in mock_run.call_args_list if "--stat" in c[0][0]]
        assert len(stat_calls) == 1
        assert "--stat-name-width=1000" in stat_calls[0]

        mock_run.reset_mock()
        commit(regenerate=True)
        stat_calls_regen = [
            c[0][0] for c in mock_run.call_args_list if "--stat" in c[0][0]
        ]
        assert len(stat_calls_regen) == 1
        assert "--stat-name-width=1000" in stat_calls_regen[0]
