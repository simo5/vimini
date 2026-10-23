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
    _extract_rename_paths,
    handle_commit_response,
    commit,
    finalize_commit,
    _revert_staged,
    _revert_amend,
    _commit_files_by_file,
    CommitChannelHandler,
)


def test_extract_rename_paths():
    assert _extract_rename_paths("{old => new}/file.txt") == (
        "old/file.txt",
        "new/file.txt",
    )
    assert _extract_rename_paths("{dir => }/file.txt") == ("dir/file.txt", "file.txt")
    assert _extract_rename_paths("{ => dir}/file.txt") == ("file.txt", "dir/file.txt")
    assert _extract_rename_paths("dir/{old => new}.py") == ("dir/old.py", "dir/new.py")
    assert _extract_rename_paths("old.txt => new.txt") == ("old.txt", "new.txt")
    assert _extract_rename_paths("dir/{old => new}") == ("dir/old", "dir/new")
    assert _extract_rename_paths("no rename here") == (None, None)
    assert _extract_rename_paths("old.txt => new.txt (100%)") == ("old.txt", "new.txt")
    assert _extract_rename_paths("{old_dir => new_dir}/file.py (95%)") == (
        "old_dir/file.py",
        "new_dir/file.py",
    )
    assert _extract_rename_paths("dir/{old => new}.py (100%)") == (
        "dir/old.py",
        "dir/new.py",
    )


def test_parse_file_line():
    commit_files = ["src/app.py", "README.md", "path/with space/file.txt"]

    # Included file
    fn, deselected = parse_file_line("#   src/app.py", commit_files)
    assert fn == "src/app.py"
    assert deselected is False

    # Deselected file with valid '#- ' indicator
    fn, deselected = parse_file_line("#- src/app.py", commit_files)
    assert fn == "src/app.py"
    assert deselected is True

    fn, deselected = parse_file_line("#-  src/app.py", commit_files)
    assert fn == "src/app.py"
    assert deselected is True

    # Invalid deselection formats: spaces between # and -, or lack of spaces after #-
    assert parse_file_line("# - src/app.py", commit_files) == (None, None)
    assert parse_file_line("# -src/app.py", commit_files) == (None, None)
    assert parse_file_line("#-src/app.py", commit_files) == (None, None)
    assert parse_file_line("#-\tsrc/app.py", commit_files) == (None, None)

    fn, deselected = parse_file_line("# src/app.py", commit_files)
    assert fn == "src/app.py"
    assert deselected is False

    # Path with space
    fn, deselected = parse_file_line("#   path/with space/file.txt", commit_files)
    assert fn == "path/with space/file.txt"
    assert deselected is False

    fn, deselected = parse_file_line("#-  path/with space/file.txt", commit_files)
    assert fn == "path/with space/file.txt"
    assert deselected is True

    # Diff stat formatted lines
    fn, deselected = parse_file_line("#   src/app.py | 5 ++---", commit_files)
    assert fn == "src/app.py"
    assert deselected is False

    fn, deselected = parse_file_line("#-  src/app.py | 5 ++---", commit_files)
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
        "#-  {old_dir => }/file.txt | 2 +-", commit_files_rename
    )
    assert fn == "file.txt"
    assert deselected is True

    fn, deselected = parse_file_line(
        "#   old_app.py => app.py | 5 ++---", commit_files_rename
    )
    assert fn == "app.py"
    assert deselected is False

    # Renamed files with similarity percentage in diff stat
    fn, deselected = parse_file_line(
        "#   old.txt => new.txt (100%) | 0", ["old.txt", "new.txt"]
    )
    assert fn == "new.txt"
    assert deselected is False

    fn, deselected = parse_file_line(
        "#-  old.txt => new.txt (100%) | 0", ["old.txt", "new.txt"]
    )
    assert fn == "new.txt"
    assert deselected is True

    fn, deselected = parse_file_line(
        "#   {old_dir => new_dir}/file.py (95%) | 2 +-",
        ["old_dir/file.py", "new_dir/file.py"],
    )
    assert fn == "new_dir/file.py"
    assert deselected is False

    # Files with leading hyphens
    commit_files_hyphens = ["-file.txt", "--flag.py"]
    fn, deselected = parse_file_line("#   -file.txt", commit_files_hyphens)
    assert fn == "-file.txt"
    assert deselected is False

    fn, deselected = parse_file_line("#   -file.txt | 2 +-", commit_files_hyphens)
    assert fn == "-file.txt"
    assert deselected is False

    fn, deselected = parse_file_line("#-  -file.txt", commit_files_hyphens)
    assert fn == "-file.txt"
    assert deselected is True

    fn, deselected = parse_file_line("#-  -file.txt | 2 +-", commit_files_hyphens)
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

    fn, deselected = parse_file_line("#-  path/to|file.txt | 2 +-", commit_files_pipe)
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
        "#-  README.md  | 5 +++--",
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
        assert vim.current.buffer[4] == "#-  src/app.py | 2 +-"

        # Toggle src/app.py again (deselected -> included, preserving stat)
        toggle_file()
        assert vim.current.buffer[4] == "#   src/app.py | 2 +-"

        # Move to row 6 ("#-  README.md  | 5 +++--") and toggle (deselected -> included)
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
        assert vim.current.buffer[4] == "#-  -file.txt | 2 +-"
        toggle_file()
        assert vim.current.buffer[4] == "#   -file.txt | 2 +-"

        vim.current.window.cursor = (6, 4)
        toggle_file()
        assert vim.current.buffer[5] == "#-  --flag.py | 1 +"
        toggle_file()
        assert vim.current.buffer[5] == "#   --flag.py | 1 +"


def test_toggle_file_pure_regenerate_emits_error():
    import vim

    commit_files = ["src/app.py"]
    mock_buffer = [
        "Commit title",
        "",
        "# --- Diff stat ---",
        "#   src/app.py | 2 +-",
    ]

    def mock_eval(expr):
        if "vimini_commit_files" in expr:
            return json.dumps(commit_files)
        if "vimini_commit_amend" in expr:
            return 0
        if "vimini_commit_regenerate" in expr:
            return 1
        if "vimini_commit_amend_files" in expr:
            return json.dumps([])
        if "vimini_commit_tmp_file" in expr:
            return ""
        return ""

    with (
        patch.object(vim.current, "buffer", mock_buffer),
        patch.object(vim, "eval", side_effect=mock_eval),
        patch("vimini.util.display_message") as mock_display,
    ):
        vim.current.window.cursor = (4, 4)
        toggle_file()
        # Line should remain unchanged
        assert vim.current.buffer[3] == "#   src/app.py | 2 +-"
        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any(
            "Files cannot be deselected in regenerate mode" in str(c) for c in err_calls
        )


def test_toggle_file_amend_allowed_and_disallowed():
    import vim

    commit_files = ["src/changed.py", "src/unchanged.py"]
    amend_files = ["src/changed.py"]
    mock_buffer = [
        "Commit title",
        "",
        "# --- Diff stat ---",
        "# (Press <Space> to toggle file inclusion)",
        "#   src/changed.py   | 2 +-",
        "#   src/unchanged.py | 1 +",
        "#-  src/unchanged.py | 1 +",
    ]

    def mock_eval(expr):
        if "vimini_commit_amend_files" in expr:
            return json.dumps(amend_files)
        if "vimini_commit_files" in expr:
            return json.dumps(commit_files)
        if "vimini_commit_amend" in expr:
            return 1
        return ""

    with (
        patch.object(vim.current, "buffer", mock_buffer),
        patch.object(vim, "eval", side_effect=mock_eval),
        patch("vimini.util.display_message") as mock_display,
    ):
        # Trying to deselect unchanged file -> disallowed
        vim.current.window.cursor = (6, 4)
        toggle_file()
        assert vim.current.buffer[5] == "#   src/unchanged.py | 1 +"
        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any(
            "This file is not being changed by the amendment" in str(c)
            for c in err_calls
        )

        # Deselecting changed file -> allowed
        mock_display.reset_mock()
        vim.current.window.cursor = (5, 4)
        toggle_file()
        assert vim.current.buffer[4] == "#-  src/changed.py   | 2 +-"
        assert not mock_display.called


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


def test_handle_commit_response_rename_no_extraneous_comment(tmp_path):
    import vim

    result = {
        "text": "Rename file\n---\nRenamed old.txt to new.txt",
        "repo_path": "/dummy/repo",
        "diff_stat_output": " old.txt => new.txt | 2 +-\n 1 file changed",
        "commit_files": ["old.txt", "new.txt"],
        "regenerate": True,
        "assistant": True,
    }

    fake_temp = tmp_path / "fake_rename.gitcommit"
    fake_temp.touch()
    mock_buffer = []
    with (
        patch.object(vim.current, "buffer", mock_buffer),
        patch("vimini.util.new_split"),
        patch.object(vim, "command"),
        patch("vimini.util.display_message"),
        patch("tempfile.NamedTemporaryFile") as mock_named_temp,
    ):
        mock_named_temp.return_value.__enter__.return_value.name = str(fake_temp)
        try:
            handle_commit_response("job_rename", result)

            assert "#   old.txt => new.txt | 2 +-" in vim.current.buffer
            # old.txt should NOT be appended as an extraneous bare line below diff stat
            assert "#   old.txt" not in vim.current.buffer
            assert "#   new.txt" not in vim.current.buffer
        finally:
            _commit_files_by_file.pop(str(fake_temp), None)
            if fake_temp.exists():
                fake_temp.unlink()


def test_handle_commit_response_rename_with_similarity_percentages(tmp_path):
    import vim

    result = {
        "text": "Rename files\n---\nRenamed files with similarity percentage.",
        "repo_path": "/dummy/repo",
        "diff_stat_output": (
            " old.txt => new.txt (100%) | 0\n"
            " {old_dir => new_dir}/file.py (95%) | 2 +-\n"
            " 2 files changed"
        ),
        "commit_files": ["new.txt", "new_dir/file.py"],
        "regenerate": True,
        "assistant": True,
    }

    fake_temp = tmp_path / "fake_rename_pct.gitcommit"
    fake_temp.touch()
    mock_buffer = []
    with (
        patch.object(vim.current, "buffer", mock_buffer),
        patch("vimini.util.new_split"),
        patch.object(vim, "command"),
        patch("vimini.util.display_message"),
        patch("tempfile.NamedTemporaryFile") as mock_named_temp,
    ):
        mock_named_temp.return_value.__enter__.return_value.name = str(fake_temp)
        try:
            handle_commit_response("job_rename_pct", result)

            assert "#   old.txt => new.txt (100%) | 0" in vim.current.buffer
            assert "#   {old_dir => new_dir}/file.py (95%) | 2 +-" in vim.current.buffer
            # Neither should be appended as bare comment lines at bottom
            assert "#   old.txt" not in vim.current.buffer
            assert "#   new.txt" not in vim.current.buffer
            assert "#   old_dir/file.py" not in vim.current.buffer
            assert "#   new_dir/file.py" not in vim.current.buffer
        finally:
            _commit_files_by_file.pop(str(fake_temp), None)
            if fake_temp.exists():
                fake_temp.unlink()


def test_handle_commit_response_pure_regenerate_no_space_hint(tmp_path):
    import vim

    result = {
        "text": "Regen subject\n---\nRegen body",
        "repo_path": "/dummy/repo",
        "diff_stat_output": " src/app.py | 2 +-\n 1 file changed",
        "commit_files": ["src/app.py"],
        "regenerate": True,
        "amend": False,
        "assistant": True,
    }

    fake_temp = tmp_path / "fake_regen_nospace.gitcommit"
    fake_temp.touch()
    mock_buffer = []
    with (
        patch.object(vim.current, "buffer", mock_buffer),
        patch("vimini.util.new_split"),
        patch.object(vim, "command"),
        patch("vimini.util.display_message") as mock_display,
        patch("tempfile.NamedTemporaryFile") as mock_named_temp,
    ):
        mock_named_temp.return_value.__enter__.return_value.name = str(fake_temp)
        try:
            handle_commit_response("job_regen_nospace", result)

            assert "# --- Diff stat ---" in vim.current.buffer
            assert (
                "# (Press <Space> to toggle file inclusion)" not in vim.current.buffer
            )
            assert "#   src/app.py | 2 +-" in vim.current.buffer
            calls = [str(c) for c in mock_display.call_args_list]
            assert any("Review the commit message. Save and close" in c for c in calls)
            assert not any("Toggle files with <Space>" in c for c in calls)
        finally:
            _commit_files_by_file.pop(str(fake_temp), None)
            if fake_temp.exists():
                fake_temp.unlink()


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
        "#-  src/excluded.py | 3 +--\n"
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


def test_finalize_commit_renamed_file_deselected_resets_both_paths(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_rename_deselected.txt"
    repo_path = str(tmp_path)
    commit_files = ["kept.py", "old.txt", "new.txt"]

    content = (
        "Commit subject\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "#   kept.py | 1 +\n"
        "#-  old.txt => new.txt | 2 +-\n"
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
        if "vimini_commit_renames" in expr:
            return json.dumps([["old.txt", "new.txt"]])
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_eval_fn),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.display_message"),
    ):
        mock_proc_ok = MagicMock(
            returncode=0, stdout="[main abc1234] Commit subject", stderr=""
        )
        mock_run.return_value = mock_proc_ok

        finalize_commit()

        run_cmds = [c[0][0] for c in mock_run.call_args_list]

        # Both source (old.txt) and target (new.txt) must be unstaged via reset
        unstage_calls = [cmd for cmd in run_cmds if "reset" in cmd]
        assert len(unstage_calls) == 1
        assert "old.txt" in unstage_calls[0]
        assert "new.txt" in unstage_calls[0]
        assert "kept.py" not in unstage_calls[0]


def test_finalize_commit_renamed_file_deleted_line_resets_both_paths(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_rename_deleted.txt"
    repo_path = str(tmp_path)
    commit_files = ["kept.py", "old.txt", "new.txt"]

    # The rename line is completely deleted from the diff stat
    content = (
        "Commit subject\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "#   kept.py | 1 +\n"
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
        if "vimini_commit_renames" in expr:
            return json.dumps([["old.txt", "new.txt"]])
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_eval_fn),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.display_message"),
    ):
        mock_proc_ok = MagicMock(
            returncode=0, stdout="[main abc1234] Commit subject", stderr=""
        )
        mock_run.return_value = mock_proc_ok

        finalize_commit()

        run_cmds = [c[0][0] for c in mock_run.call_args_list]

        unstage_calls = [cmd for cmd in run_cmds if "reset" in cmd]
        assert len(unstage_calls) == 1
        assert "old.txt" in unstage_calls[0]
        assert "new.txt" in unstage_calls[0]
        assert "kept.py" not in unstage_calls[0]


def test_finalize_commit_included_file_starting_with_hyphen(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_hyphen.txt"
    repo_path = str(tmp_path)
    commit_files = ["-old.txt", "new.txt", "-standalone.py"]

    # Included files starting with hyphens:
    # 1. Renamed file starting with hyphen: -old.txt => new.txt
    # 2. Standalone file starting with hyphen: -standalone.py
    content = (
        "Commit subject\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "#   -old.txt => new.txt | 2 +-\n"
        "#   -standalone.py      | 1 +\n"
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
        if "vimini_commit_renames" in expr:
            return json.dumps([["-old.txt", "new.txt"]])
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_eval_fn),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.display_message"),
    ):
        mock_proc_ok = MagicMock(
            returncode=0, stdout="[main abc1234] Commit subject", stderr=""
        )
        mock_run.return_value = mock_proc_ok

        finalize_commit()

        run_cmds = [c[0][0] for c in mock_run.call_args_list]

        # No unstage reset calls should occur since all files were included
        unstage_calls = [cmd for cmd in run_cmds if "reset" in cmd]
        assert len(unstage_calls) == 0

        # Commit should proceed with all files
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
        "#-  file1.py | 1 +\n"
        "#-  file2.py | 1 -\n"
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


def test_finalize_commit_pure_regenerate_disallows_deselection(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_regen.txt"
    repo_path = str(tmp_path)
    commit_files = ["fileA.py", "fileB.py"]

    content = (
        "Amended subject\n\n"
        "# --- Diff stat ---\n"
        "#   fileA.py | 1 +\n"
        "#-  fileB.py | 1 -\n"
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

        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any(
            "Files cannot be deselected in regenerate mode" in str(c) for c in err_calls
        )

        run_cmds = [c[0][0] for c in mock_run.call_args_list]
        assert not any("commit" in cmd for cmd in run_cmds)
        assert not any("HEAD~1" in cmd for cmd in run_cmds)


def test_finalize_commit_pure_regenerate_disallows_deleted_diff_stat_line(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_regen_deleted.txt"
    repo_path = str(tmp_path)
    commit_files = ["fileA.py", "fileB.py"]

    # fileB.py deleted from diff stat
    content = "Amended subject\n\n# --- Diff stat ---\n#   fileA.py | 1 +\n"
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
        finalize_commit()

        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any(
            "Files cannot be deselected in regenerate mode" in str(c) for c in err_calls
        )

        run_cmds = [c[0][0] for c in mock_run.call_args_list]
        assert not any("commit" in cmd for cmd in run_cmds)


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
        "#-  src/excluded.py | 1 -\n"
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
        "#-  src/excluded.py | 1 -\n"
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
        "#-  f1.py | 1 +\n"
        "#-  f2.py | 1 -\n"
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

    content = "Regen subject\n\n# --- Diff stat ---\n#   f1.py | 1 +\n#   f2.py | 1 -\n"
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


def test_finalize_commit_outer_exception_reverts_staged(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_outer_exc.txt"
    repo_path = str(tmp_path)
    commit_files = ["file1.py"]

    content = (
        "Commit subject\n\n"
        "Commit body\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "#   file1.py | 1 +\n"
    )
    tmp_file.write_text(content, encoding="utf-8")

    def mock_eval_fn(expr):
        if "vimini_commit_tmp_file" in expr:
            return str(tmp_file)
        if "vimini_commit_repo_path" in expr:
            return repo_path
        if "vimini_commit_regenerate" in expr:
            return 0
        if "vimini_commit_amend" in expr:
            return 0
        if "vimini_commit_files" in expr:
            return json.dumps(commit_files)
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_eval_fn),
        patch("subprocess.run") as mock_run,
        patch("vimini.commit._revert_staged") as mock_revert_staged,
        patch("vimini.util.display_message") as mock_display,
    ):
        mock_run.side_effect = RuntimeError("unexpected subprocess error")

        finalize_commit()

        assert mock_revert_staged.called
        mock_revert_staged.assert_called_once_with(repo_path)
        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any("unexpected subprocess error" in str(c) for c in err_calls)


def test_finalize_commit_missing_diff_stat_header_reverts_staged(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_no_header.txt"
    repo_path = str(tmp_path)
    commit_files = ["file1.py"]

    # Diff stat header "# --- Diff stat ---" was removed/omitted
    content = "Commit subject\n\nCommit body\n#   file1.py | 1 +\n"
    tmp_file.write_text(content, encoding="utf-8")

    def mock_eval_fn(expr):
        if "vimini_commit_tmp_file" in expr:
            return str(tmp_file)
        if "vimini_commit_repo_path" in expr:
            return repo_path
        if "vimini_commit_regenerate" in expr:
            return 0
        if "vimini_commit_amend" in expr:
            return 0
        if "vimini_commit_files" in expr:
            return json.dumps(commit_files)
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_eval_fn),
        patch("subprocess.run") as mock_run,
        patch("vimini.commit._revert_staged") as mock_revert_staged,
        patch("vimini.util.display_message") as mock_display,
    ):
        finalize_commit()

        assert mock_revert_staged.called
        mock_revert_staged.assert_called_once_with(repo_path)
        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any(
            "commit was malformed: missing diff stat header" in str(c)
            for c in err_calls
        )
        assert not any("commit" in c[0][0] for c in mock_run.call_args_list)


def test_finalize_commit_missing_diff_stat_header_reverts_amend(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_no_header_amend.txt"
    repo_path = str(tmp_path)
    orig_head = "sha_orig_123"
    commit_files = ["file1.py"]

    # Diff stat header "# --- Diff stat ---" was removed/omitted
    content = "Commit subject\n\nCommit body\n#   file1.py | 1 +\n"
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
        if "vimini_commit_files" in expr:
            return json.dumps(commit_files)
        return ""

    with (
        patch.object(vim, "eval", side_effect=mock_eval_fn),
        patch("subprocess.run") as mock_run,
        patch("vimini.commit._revert_amend") as mock_revert_amend,
        patch("vimini.util.display_message") as mock_display,
    ):
        finalize_commit()

        assert mock_revert_amend.called
        mock_revert_amend.assert_called_once_with(repo_path, orig_head)
        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any(
            "commit was malformed: missing diff stat header" in str(c)
            for c in err_calls
        )
        assert not any("commit" in c[0][0] for c in mock_run.call_args_list)


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
        assert "--stat-width=1000" in stat_calls[0]
        assert "--stat-name-width=1000" in stat_calls[0]
        assert "--stat-graph-width=10" in stat_calls[0]
        assert "-c" in stat_calls[0]
        assert "core.quotepath=false" in stat_calls[0]

        mock_run.reset_mock()
        commit(regenerate=True)
        stat_calls_regen = [
            c[0][0] for c in mock_run.call_args_list if "--stat" in c[0][0]
        ]
        assert len(stat_calls_regen) == 1
        assert "--stat-width=1000" in stat_calls_regen[0]
        assert "--stat-name-width=1000" in stat_calls_regen[0]
        assert "--stat-graph-width=10" in stat_calls_regen[0]
        assert "-c" in stat_calls_regen[0]
        assert "core.quotepath=false" in stat_calls_regen[0]


def test_commit_amend_initial_commit_failure_reverts_staged(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("vimini.commit._run_format_command", return_value=True),
        patch("vimini.commit._stage_changes", return_value=True),
        patch("vimini.commit._revert_staged") as mock_revert_staged,
        patch("vimini.util.display_message") as mock_display,
        patch("subprocess.run") as mock_run,
    ):
        mock_rev_parse = MagicMock(returncode=0, stdout="current_head_sha\n", stderr="")
        mock_amend_fail = MagicMock(
            returncode=1, stderr="pre-commit hook failed", stdout=""
        )

        def fake_run(cmd, *args, **kwargs):
            if "rev-parse" in cmd:
                return mock_rev_parse
            if "commit" in cmd and "--amend" in cmd:
                return mock_amend_fail
            return MagicMock(returncode=0, stdout="", stderr="")

        mock_run.side_effect = fake_run

        commit(amend=True)

        assert mock_revert_staged.called
        mock_revert_staged.assert_called_once_with(repo_path)
        err_calls = [c for c in mock_display.call_args_list if c.kwargs.get("error")]
        assert any("Git amend failed" in str(c) for c in err_calls)


def test_commit_channel_handler_handle_response_exception_reverts_amend():
    handler = CommitChannelHandler(
        "job_err",
        orig_head="head_sha_123",
        amend=True,
        repo_path="/dummy/repo",
    )
    with (
        patch(
            "vimini.commit.handle_commit_response",
            side_effect=RuntimeError("Vim buffer split failed"),
        ),
        patch("vimini.commit._revert_amend") as mock_revert_amend,
        patch("vimini.util.display_message") as mock_display,
    ):
        handler.handle_response({"text": "Commit msg"})
        assert mock_revert_amend.called
        mock_revert_amend.assert_called_once_with("/dummy/repo", "head_sha_123")
        assert any(
            "Vim buffer split failed" in str(c) for c in mock_display.call_args_list
        )


def test_commit_channel_handler_handle_response_exception_reverts_staged():
    handler = CommitChannelHandler(
        "job_err2",
        amend=False,
        repo_path="/dummy/repo",
    )
    with (
        patch(
            "vimini.commit.handle_commit_response",
            side_effect=RuntimeError("Tempfile error"),
        ),
        patch("vimini.commit._revert_staged") as mock_revert_staged,
        patch("vimini.util.display_message") as mock_display,
    ):
        handler.handle_response({"text": "Commit msg", "regenerate": False})
        assert mock_revert_staged.called
        mock_revert_staged.assert_called_once_with("/dummy/repo")


def test_commit_channel_handler_handle_error_regenerate_does_not_unstage():
    handler = CommitChannelHandler(
        "job_err_regen",
        amend=False,
        regenerate=True,
        repo_path="/dummy/repo",
    )
    with (
        patch("vimini.commit._revert_staged") as mock_revert_staged,
        patch("vimini.commit._revert_amend") as mock_revert_amend,
        patch("vimini.util.display_message") as mock_display,
    ):
        handler.handle_error({"message": "Server error"})
        assert not mock_revert_staged.called
        assert not mock_revert_amend.called
        assert any("Server error" in str(c) for c in mock_display.call_args_list)


def test_commit_channel_handler_handle_error_reverts_staged_when_not_regenerate():
    handler = CommitChannelHandler(
        "job_err_normal",
        amend=False,
        regenerate=False,
        repo_path="/dummy/repo",
    )
    with (
        patch("vimini.commit._revert_staged") as mock_revert_staged,
        patch("vimini.util.display_message") as mock_display,
    ):
        handler.handle_error({"message": "Server error"})
        assert mock_revert_staged.called
        mock_revert_staged.assert_called_once_with("/dummy/repo")


def test_commit_channel_handler_handle_error_reverts_amend():
    handler = CommitChannelHandler(
        "job_err_amend",
        orig_head="orig_sha",
        amend=True,
        regenerate=True,
        repo_path="/dummy/repo",
    )
    with (
        patch("vimini.commit._revert_amend") as mock_revert_amend,
        patch("vimini.util.display_message") as mock_display,
    ):
        handler.handle_error({"message": "Server error"})
        assert mock_revert_amend.called
        mock_revert_amend.assert_called_once_with("/dummy/repo", "orig_sha")


def test_finalize_commit_comment_in_body_not_treated_as_deselection(tmp_path):
    import vim

    tmp_file = tmp_path / "commit_msg_comment_body.txt"
    repo_path = str(tmp_path)
    commit_files = ["src/included.py"]

    # Body contains a comment line that looks like deselection: #- src/included.py
    content = (
        "Commit subject\n\n"
        "Commit body\n"
        "#- src/included.py was refactored\n\n"
        "# --- Diff stat ---\n"
        "# (Press <Space> to toggle file inclusion)\n"
        "#   src/included.py | 2 +-\n"
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

        run_cmds = [c[0][0] for c in mock_run.call_args_list]
        unstage_calls = [cmd for cmd in run_cmds if "reset" in cmd]
        assert len(unstage_calls) == 0

        commit_calls = [cmd for cmd in run_cmds if "commit" in cmd and "-s" in cmd]
        assert len(commit_calls) == 1


def test_real_git_commits_workflow(tmp_path):
    import vim
    import subprocess
    from vimini.handler import get_handler

    # 1. Initialize a new tree with a single file (ensure test repo has no other files)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "-C", str(repo), "init", "-b", "main"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True
    )
    (repo / "init.txt").write_text("initial file\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init commit"], check=True)

    init_commit_sha = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    # Helper to simulate Vimini commit loop
    mock_eval_dict = {}

    def fake_cmd(cmd_str):
        if cmd_str.startswith("let "):
            var_part = cmd_str[4:].strip()
            k, v = var_part.split(" = ", 1)
            if k.startswith("b:"):
                k = k[2:]
            if (v.startswith("'") and v.endswith("'")) or (
                v.startswith('"') and v.endswith('"')
            ):
                v = v[1:-1]
            elif v.isdigit():
                v = int(v)
            mock_eval_dict[k] = v

    def fake_eval(expr):
        for k in sorted(mock_eval_dict.keys(), key=len, reverse=True):
            if k in expr:
                return mock_eval_dict[k]
        return ""

    mock_buf = []

    def run_commit_prompt(commit_kwargs, msg="A commit\n---\nBody"):
        mock_eval_dict.clear()
        mock_buf.clear()

        def fake_send(req):
            job_id = req["id"]
            handler = get_handler(job_id)
            handler.handle_response(
                {
                    "text": msg,
                    "repo_path": req["params"]["repo_path"],
                    "diff_stat_output": req["params"]["diff_stat_output"],
                    "commit_files": req["params"]["commit_files"],
                    "regenerate": req["params"]["regenerate"],
                    "amend": req["params"]["amend"],
                    "orig_head": req["params"]["orig_head"],
                    "assistant": False,
                }
            )
            return True

        with (
            patch("vimini.util.get_git_repo_root", return_value=str(repo)),
            patch.object(vim.current, "buffer", mock_buf),
            patch("vimini.util.send_channel_request", side_effect=fake_send),
            patch("vimini.util.new_split"),
            patch.object(vim, "command", side_effect=fake_cmd),
        ):
            commit(assistant=False, **commit_kwargs)
        return mock_eval_dict.get("vimini_commit_tmp_file")

    # 2. Add one file and commit (no regen, no amend), abort the commit, ensure no file was committed.
    (repo / "f_abort.txt").write_text("abort file\n")
    tmp_file_2 = run_commit_prompt({})
    assert tmp_file_2 and os.path.exists(tmp_file_2)
    # Abort by saving empty content (no commit message)
    with open(tmp_file_2, "w", encoding="utf-8") as f:
        f.write("# empty commit message\n")
    with (
        patch("vimini.util.get_git_repo_root", return_value=str(repo)),
        patch.object(vim, "eval", side_effect=fake_eval),
    ):
        finalize_commit()

    head_after_2 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_after_2 == init_commit_sha
    (repo / "f_abort.txt").unlink()
    subprocess.run(["git", "-C", str(repo), "reset", "HEAD", "--"], check=True)

    # 3. Add two files, make a commit (no regen, no amend), deselect second file,
    # complete commit, ensure second file not committed, first file is.
    (repo / "f1.txt").write_text("f1 content\n")
    (repo / "f2.txt").write_text("f2 content\n")
    tmp_file_3 = run_commit_prompt({})
    # Deselect f2.txt
    for idx, line in enumerate(mock_buf):
        if "f2.txt" in line:
            mock_buf[idx] = line.replace("#   ", "#-  ")
    with open(tmp_file_3, "w", encoding="utf-8") as f:
        f.write("\n".join(mock_buf) + "\n")
    with (
        patch("vimini.util.get_git_repo_root", return_value=str(repo)),
        patch.object(vim, "eval", side_effect=fake_eval),
    ):
        finalize_commit()

    head_after_3 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_after_3 != init_commit_sha
    show_head_3 = subprocess.run(
        ["git", "-C", str(repo), "show", "--name-only", "--pretty=", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert "f1.txt" in show_head_3
    assert "f2.txt" not in show_head_3

    # 4. Do the same as (3), if all files are deselected this should completely abort
    # ensure no empty commit was added
    (repo / "f3.txt").write_text("f3 content\n")
    tmp_file_4 = run_commit_prompt({})
    # Deselect all files in buffer
    for idx, line in enumerate(mock_buf):
        if "f2.txt" in line or "f3.txt" in line:
            mock_buf[idx] = line.replace("#   ", "#-  ")
    with open(tmp_file_4, "w", encoding="utf-8") as f:
        f.write("\n".join(mock_buf) + "\n")
    with (
        patch("vimini.util.get_git_repo_root", return_value=str(repo)),
        patch.object(vim, "eval", side_effect=fake_eval),
    ):
        finalize_commit()

    head_after_4 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert head_after_4 == head_after_3
    (repo / "f3.txt").unlink()
    subprocess.run(["git", "-C", str(repo), "reset", "HEAD", "--"], check=True)

    # 5. Change 2 files, and "amend" the commit, deselect 1 file, and continue the
    # commit, ensure that only the change to that file is missing from the commit,
    # and the new commit includes all the changes from the old commit and the single
    # file that was amended.
    # f1.txt is in HEAD (old commit). f2.txt is uncommitted.
    # We modify f1.txt and f2.txt.
    (repo / "f1.txt").write_text("f1 modified\n")
    (repo / "f2.txt").write_text("f2 modified\n")
    tmp_file_5 = run_commit_prompt({"amend": True})
    # Deselect f1.txt (which was part of the old commit!)
    for idx, line in enumerate(mock_buf):
        if "f1.txt" in line:
            mock_buf[idx] = line.replace("#   ", "#-  ")
    with open(tmp_file_5, "w", encoding="utf-8") as f:
        f.write("\n".join(mock_buf) + "\n")
    with (
        patch("vimini.util.get_git_repo_root", return_value=str(repo)),
        patch.object(vim, "eval", side_effect=fake_eval),
    ):
        finalize_commit()

    # Ensure HEAD was amended: f1.txt should have the old commit's content ("f1 content\n"),
    # and f2.txt should have the new content ("f2 modified\n")
    f1_head_5 = subprocess.run(
        ["git", "-C", str(repo), "show", "HEAD:f1.txt"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert f1_head_5 == "f1 content\n"
    f2_head_5 = subprocess.run(
        ["git", "-C", str(repo), "show", "HEAD:f2.txt"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert f2_head_5 == "f2 modified\n"
    assert (repo / "f1.txt").read_text() == "f1 modified\n"

    # 6. Commit with regenerate only, ensure only the commit message was changed and all
    # the contents are the same.
    tree_before_6 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    tmp_file_6 = run_commit_prompt(
        {"regenerate": True}, msg="Regenerated commit message\n---\nNew body"
    )
    assert "# (Press <Space> to toggle file inclusion)" not in mock_buf
    # In pure regenerate mode, ensure trying to toggle any file emits an error
    with (
        patch.object(vim.current, "buffer", mock_buf),
        patch("vimini.util.display_message") as mock_display,
        patch.object(vim, "eval", side_effect=fake_eval),
        patch.object(vim, "command"),
    ):
        for idx, line in enumerate(mock_buf):
            if "f2.txt" in line:
                vim.current.window.cursor = (idx + 1, 4)
                toggle_file()
                assert "f2.txt" in mock_buf[idx]
                assert not mock_buf[idx].startswith("#-")
                err_calls = [
                    c for c in mock_display.call_args_list if c.kwargs.get("error")
                ]
                assert any(
                    "Files cannot be deselected in regenerate mode" in str(c)
                    for c in err_calls
                )
    with open(tmp_file_6, "w", encoding="utf-8") as f:
        f.write("\n".join(mock_buf) + "\n")
    with (
        patch("vimini.util.get_git_repo_root", return_value=str(repo)),
        patch.object(vim, "eval", side_effect=fake_eval),
    ):
        finalize_commit()

    tree_after_6 = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD^{tree}"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert tree_after_6 == tree_before_6
    msg_after_6 = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--pretty=%s"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    assert msg_after_6 == "Regenerated commit message"
