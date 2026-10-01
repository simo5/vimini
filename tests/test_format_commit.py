import sys
import os
import pytest
from unittest.mock import MagicMock, patch

# Ensure python3 root is in sys.path
sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini.commit import _run_format_command, commit
from vimini.agent.chat import ChatSession


def test_run_format_command_no_config(tmp_path):
    repo_path = str(tmp_path)
    with patch(
        "vimini.common.util.load_project_data", return_value={"configuration": {}}
    ):
        res = _run_format_command(repo_path)
        assert res is True


def test_run_format_command_success(tmp_path):
    repo_path = str(tmp_path)
    project_data = {"configuration": {"format-command": "echo 'formatting'"}}
    with (
        patch("vimini.common.util.load_project_data", return_value=project_data),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(returncode=0, stdout="formatted", stderr="")
        res = _run_format_command(repo_path)
        assert res is True
        mock_run.assert_called_once()
        assert "echo 'formatting'" in mock_run.call_args[0][0]


def test_run_format_command_failure(tmp_path):
    repo_path = str(tmp_path)
    project_data = {"configuration": {"format-command": "echo 'error' && exit 1"}}
    with (
        patch("vimini.common.util.load_project_data", return_value=project_data),
        patch("subprocess.run") as mock_run,
        patch("vimini.util.display_message") as mock_display,
    ):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="format error"
        )
        res = _run_format_command(repo_path)
        assert res is False
        mock_display.assert_called()
        err_args = [
            call for call in mock_display.call_args_list if call.kwargs.get("error")
        ]
        assert len(err_args) > 0


def test_commit_aborts_on_format_failure(tmp_path):
    repo_path = str(tmp_path)
    with (
        patch("vimini.util.get_git_repo_root", return_value=repo_path),
        patch("vimini.commit._run_format_command", return_value=False) as mock_format,
        patch("vimini.commit._stage_changes") as mock_stage,
    ):
        commit()
        mock_format.assert_called_once_with(repo_path)
        mock_stage.assert_not_called()


def test_chat_session_execute_fix_format(tmp_path):
    repo_path = str(tmp_path)
    session = ChatSession(req_id="fmt123", result_queue=MagicMock())
    session.project_root = repo_path
    session.send_response = MagicMock()

    with patch("subprocess.Popen") as mock_popen:
        mock_proc = MagicMock()
        mock_proc.stdout.readline.side_effect = ["Formatted file.py\n", ""]
        mock_proc.returncode = 0
        mock_popen.return_value = mock_proc

        output = session.execute_project_tool(
            "fix_format", "black .", "fmt123", MagicMock()
        )
        assert "executed successfully" in output
        assert "Formatted file.py" in output
