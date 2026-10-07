import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini.common.util import list_git_commits, get_git_commit
from vimini.agent.review import review_tools, _execute_review_stream
from vimini.agent.chat import get_agent_tools, ChatSession


def make_mock_chunk(text=None, thought=None, function_call=None):
    parts = []
    if thought:
        thought_part = MagicMock()
        thought_part.thought = True
        thought_part.text = thought
        thought_part.function_call = None
        parts.append(thought_part)
    if text:
        text_part = MagicMock()
        text_part.thought = False
        text_part.text = text
        text_part.function_call = None
        parts.append(text_part)
    if function_call:
        fc_part = MagicMock()
        fc_part.thought = False
        fc_part.function_call = function_call
        parts.append(fc_part)

    candidate = MagicMock()
    candidate.content = MagicMock()
    candidate.content.parts = parts

    chunk = MagicMock()
    chunk.candidates = [candidate]
    chunk.text = None
    return chunk


def test_list_git_commits_not_in_git_repo():
    with patch("vimini.common.util.get_git_repo_root", return_value=None):
        res = list_git_commits("/nonexistent")
        assert "Error: Not inside a git repository." in res


def test_list_git_commits_security_rejections():
    with patch("vimini.common.util.get_git_repo_root", return_value="/repo"):
        # Rejects revision starting with -
        res = list_git_commits("/repo", revision="--all")
        assert "Security error" in res

        # Rejects revision with invalid characters
        res = list_git_commits("/repo", revision="HEAD; rm -rf /")
        assert "Security error" in res

        # Rejects file_path starting with -
        res = list_git_commits("/repo", file_path="--hard")
        assert "Security error" in res


def test_list_git_commits_success():
    mock_output = "a1b2c3d | Alice Dev | 2025-05-10 | Add user authentication\ne4f5g6h | Bob Coder | 2025-05-09 | Fix bug"
    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stdout = mock_output

    with (
        patch("vimini.common.util.get_git_repo_root", return_value="/repo"),
        patch("subprocess.run", return_value=mock_run) as run_spy,
    ):
        res = list_git_commits("/repo", max_count=5, revision="main", file_path="src/app.py")
        assert res == mock_output
        run_spy.assert_called_once()
        cmd_called = run_spy.call_args[0][0]
        assert cmd_called == [
            "git",
            "-C",
            "/repo",
            "log",
            "-n5",
            "--pretty=format:%h | %an | %ad | %s",
            "--date=short",
            "main",
            "--",
            "src/app.py",
        ]


def test_list_git_commits_empty():
    mock_run = MagicMock()
    mock_run.returncode = 0
    mock_run.stdout = "   \n"

    with (
        patch("vimini.common.util.get_git_repo_root", return_value="/repo"),
        patch("subprocess.run", return_value=mock_run),
    ):
        res = list_git_commits("/repo")
        assert res == "No commits found."


def test_get_git_commit_not_in_git_repo():
    with patch("vimini.common.util.get_git_repo_root", return_value=None):
        res = get_git_commit("abc1234", "/nonexistent")
        assert "Error: Not inside a git repository." in res


def test_get_git_commit_security_rejections():
    with patch("vimini.common.util.get_git_repo_root", return_value="/repo"):
        # Missing commit_id
        res = get_git_commit("", "/repo")
        assert "Error: commit_id parameter is required." in res

        # Rejects commit_id starting with -
        res = get_git_commit("-p", "/repo")
        assert "Security error" in res

        # Rejects commit_id with invalid characters
        res = get_git_commit("abc && touch /tmp/bad", "/repo")
        assert "Security error" in res

        # Rejects file_path starting with -
        res = get_git_commit("abc1234", "/repo", file_path="--stat")
        assert "Security error" in res


def test_get_git_commit_success():
    mock_show = MagicMock()
    mock_show.returncode = 0
    mock_show.stdout = "commit a1b2c3d\nAuthor: Alice\n\n    Add auth\n\n--- a/app.py\n+++ b/app.py\n@@ -1 +1,2 @@\n+auth\n"

    with (
        patch("vimini.common.util.get_git_repo_root", return_value="/repo"),
        patch("subprocess.run", return_value=mock_show) as run_spy,
    ):
        res = get_git_commit("a1b2c3d", "/repo", file_path="app.py", stat_only=False)
        assert res == mock_show.stdout
        cmd_called = run_spy.call_args[0][0]
        assert cmd_called == [
            "git",
            "-C",
            "/repo",
            "show",
            "--stat",
            "--patch",
            "a1b2c3d",
            "--",
            "app.py",
        ]


def test_get_git_commit_stat_only():
    mock_show = MagicMock()
    mock_show.returncode = 0
    mock_show.stdout = "commit a1b2c3d\n app.py | 2 +-\n 1 file changed\n"

    with (
        patch("vimini.common.util.get_git_repo_root", return_value="/repo"),
        patch("subprocess.run", return_value=mock_show) as run_spy,
    ):
        res = get_git_commit("a1b2c3d", "/repo", stat_only=True)
        assert res == mock_show.stdout
        cmd_called = run_spy.call_args[0][0]
        assert cmd_called == [
            "git",
            "-C",
            "/repo",
            "show",
            "--stat",
            "a1b2c3d",
        ]


def test_get_git_commit_truncation():
    lines = [f"line {i}" for i in range(1200)]
    mock_show = MagicMock()
    mock_show.returncode = 0
    mock_show.stdout = "\n".join(lines)

    with (
        patch("vimini.common.util.get_git_repo_root", return_value="/repo"),
        patch("subprocess.run", return_value=mock_show),
    ):
        res = get_git_commit("a1b2c3d", "/repo", max_lines=100)
        assert "Output truncated at 100 lines (total 1200 lines)" in res
        assert "line 99" in res
        assert "line 100" not in res.split("\n\n[Output truncated")[0]


def test_review_tools_contain_git_tools():
    tool_names = [f.name for f in review_tools[0].function_declarations]
    assert "list_git_commits" in tool_names
    assert "get_git_commit" in tool_names


def test_chat_tools_contain_git_tools():
    agent_tools = get_agent_tools()
    tool_names = [f.name for f in agent_tools[0].function_declarations]
    assert "list_git_commits" in tool_names
    assert "get_git_commit" in tool_names


def test_execute_review_stream_git_tools():
    mock_session = MagicMock()
    mock_session._check_running.return_value = True

    # Turn 1: model calls list_git_commits
    call1 = MagicMock()
    call1.name = "list_git_commits"
    call1.args = {"max_count": 5}
    chunk1 = make_mock_chunk(function_call=call1)

    # Turn 2: model calls get_git_commit
    call2 = MagicMock()
    call2.name = "get_git_commit"
    call2.args = {"commit_id": "abc1234"}
    chunk2 = make_mock_chunk(function_call=call2)

    # Turn 3: model gives final review text
    chunk3 = make_mock_chunk(text="Review completed after checking history.")

    mock_chat = MagicMock()
    mock_chat.send_message_stream.side_effect = [
        [chunk1],
        [chunk2],
        [chunk3],
    ]

    with (
        patch("vimini.agent.review.list_git_commits", return_value="abc1234 | Alice | 2025-05-10 | Init") as mock_list,
        patch("vimini.agent.review.get_git_commit", return_value="commit abc1234\nAuthor: Alice\n") as mock_get,
    ):
        res = _execute_review_stream(
            session=mock_session,
            req_id="rev_git",
            conn="conn1",
            chat_session=mock_chat,
            initial_prompt="Review history",
            project_root="/repo",
            verbose=False,
            save=False,
        )

    assert res == ""
    mock_list.assert_called_once_with(
        project_root="/repo", max_count=5, revision="HEAD", file_path=None
    )
    mock_get.assert_called_once_with(
        commit_id="abc1234", project_root="/repo", file_path=None, stat_only=False
    )


def test_chat_channel_handler_auto_approves_git_tools():
    from vimini.chat import ChatChannelHandler

    handler = ChatChannelHandler("chat_123")
    handler.get_buffer = MagicMock(return_value=MagicMock())

    with patch("vimini.chat.send_agent_approval") as mock_approval:
        handler.handle_response({"status": "tool_use_requested", "tool": "list_git_commits", "args": {}})
        mock_approval.assert_called_with(True, "chat_123")

        mock_approval.reset_mock()
        handler.handle_response({"status": "tool_use_requested", "tool": "get_git_commit", "args": {"commit_id": "a1b2"}})
        mock_approval.assert_called_with(True, "chat_123")


def test_chat_session_executes_git_tools():
    mock_queue = MagicMock()
    session = ChatSession("chat_git", mock_queue, agent_config={"model": "gemini-test"})
    session.send_response = MagicMock()
    session.project_root = "/repo"

    mock_client = MagicMock()
    mock_chat = MagicMock()
    mock_client.chats.create.return_value = mock_chat
    session.client = mock_client
    session.session = mock_chat

    # Turn 1: model requests list_git_commits
    call1 = MagicMock()
    call1.name = "list_git_commits"
    call1.args = {"max_count": 3}
    chunk1 = make_mock_chunk(function_call=call1)

    # Turn 2: model requests get_git_commit
    call2 = MagicMock()
    call2.name = "get_git_commit"
    call2.args = {"commit_id": "def5678"}
    chunk2 = make_mock_chunk(function_call=call2)

    # Turn 3: model finishes response
    chunk3 = make_mock_chunk(text="Here is the commit summary.")

    mock_chat.send_message_stream.side_effect = [
        [chunk1],
        [chunk2],
        [chunk3],
    ]

    # Simulate user auto-approvals in cmd_queue
    session.cmd_queue.put(("chat_git", {"approved": True}, "conn"))
    session.cmd_queue.put(("chat_git", {"approved": True}, "conn"))

    with (
        patch("vimini.agent.chat.get_client", return_value=mock_client),
        patch("vimini.agent.chat.list_git_commits", return_value="def5678 | Bob | 2025-05-10 | Fix") as mock_list,
        patch("vimini.agent.chat.get_git_commit", return_value="commit def5678\nAuthor: Bob\n") as mock_get,
    ):
        session._process_command("chat_git", {"prompt": "Show last commit"}, "conn")

    mock_list.assert_called_once_with(
        project_root="/repo",
        max_count=3,
        revision="HEAD",
        file_path=None,
    )
    mock_get.assert_called_once_with(
        commit_id="def5678",
        project_root="/repo",
        file_path=None,
        stat_only=False,
    )

    # Verify tool_use_requested was sent for both tools
    sent_tools = [
        call_args[1]["result"].get("tool")
        for call_args in session.send_response.call_args_list
        if call_args[1].get("result", {}).get("status") == "tool_use_requested"
    ]
    assert sent_tools == ["list_git_commits", "get_git_commit"]

    # Verify final chunk and done were sent
    statuses = [
        call_args[1]["result"].get("status")
        for call_args in session.send_response.call_args_list
    ]
    assert "chunk" in statuses
    assert "done" in statuses
