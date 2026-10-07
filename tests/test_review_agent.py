import sys
import os
import pytest
from unittest.mock import MagicMock, patch, call
from google.genai import types

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini.agent.review import _execute_review_stream, ReviewSession


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


def test_execute_review_stream_interactive_streams():
    mock_session = MagicMock()
    mock_session._check_running.return_value = True

    chunk1 = make_mock_chunk(thought="Analyzing the diff...")
    chunk2 = make_mock_chunk(text="Looks good to me.")

    mock_chat = MagicMock()
    mock_chat.send_message_stream.return_value = [chunk1, chunk2]

    res = _execute_review_stream(
        session=mock_session,
        req_id="123",
        conn="conn1",
        chat_session=mock_chat,
        initial_prompt="Review this",
        project_root="/repo",
        verbose=True,
        save=False,
    )

    assert res == ""

    assert mock_session.send_response.call_count == 2
    mock_session.send_response.assert_has_calls(
        [
            call(
                "123",
                "conn1",
                result={
                    "status": "thought",
                    "thought": "Analyzing the diff...",
                    "verbose": True,
                },
            ),
            call(
                "123",
                "conn1",
                result={"status": "chunk", "text": "Looks good to me."},
            ),
        ]
    )


def test_execute_review_stream_batch_mode_batches_output():
    mock_session = MagicMock()
    mock_session._check_running.return_value = True

    chunk1 = make_mock_chunk(thought="Analyzing the diff...")
    chunk2 = make_mock_chunk(text="Commit review content part 1. ")
    chunk3 = make_mock_chunk(text="Commit review content part 2.")

    mock_chat = MagicMock()
    mock_chat.send_message_stream.return_value = [chunk1, chunk2, chunk3]

    res = _execute_review_stream(
        session=mock_session,
        req_id="456",
        conn="conn2",
        chat_session=mock_chat,
        initial_prompt="Review commit",
        project_root="/repo",
        verbose=True,
        save=True,
    )

    # Batched content is accumulated and returned
    assert res == "Commit review content part 1. Commit review content part 2."
    # Neither thought nor chunk should be sent to client when save=True
    mock_session.send_response.assert_not_called()


def test_execute_review_stream_tool_calls_interactive():
    mock_session = MagicMock()
    mock_session._check_running.return_value = True

    # First turn: model requests tool execution
    tool_call = MagicMock()
    tool_call.name = "read_file"
    tool_call.args = {"filepath": "foo.py"}
    chunk1 = make_mock_chunk(function_call=tool_call)

    # Second turn: model provides final review
    chunk2 = make_mock_chunk(text="Reviewed foo.py successfully.")

    mock_chat = MagicMock()
    mock_chat.send_message_stream.side_effect = [
        [chunk1],
        [chunk2],
    ]

    with patch("vimini.agent.review.read_file", return_value="file contents here"):
        res = _execute_review_stream(
            session=mock_session,
            req_id="789",
            conn="conn3",
            chat_session=mock_chat,
            initial_prompt="Review file",
            project_root="/repo",
            verbose=False,
            save=False,
        )

    assert res == ""
    # Verifies tool_use_requested sent and chunk sent
    assert mock_session.send_response.call_count == 2
    call1 = mock_session.send_response.call_args_list[0]
    assert call1[0][0] == "789"
    assert call1[1]["result"]["status"] == "tool_use_requested"
    assert call1[1]["result"]["tool"] == "read_file"
    assert call1[1]["result"]["args"] == {"filepath": "foo.py"}

    call2 = mock_session.send_response.call_args_list[1]
    assert call2[0][0] == "789"
    assert call2[1]["result"]["status"] == "chunk"
    assert call2[1]["result"]["text"] == "Reviewed foo.py successfully."


def test_execute_review_stream_tool_calls_batch_save():
    mock_session = MagicMock()
    mock_session._check_running.return_value = True

    tool_call = MagicMock()
    tool_call.name = "list_directory"
    tool_call.args = {"directory_path": "src"}
    chunk1 = make_mock_chunk(function_call=tool_call)
    chunk2 = make_mock_chunk(text="Batch review output.")

    mock_chat = MagicMock()
    mock_chat.send_message_stream.side_effect = [
        [chunk1],
        [chunk2],
    ]

    with patch("vimini.agent.review.list_directory", return_value="foo.py\nbar.py"):
        res = _execute_review_stream(
            session=mock_session,
            req_id="999",
            conn="conn4",
            chat_session=mock_chat,
            initial_prompt="Review dir",
            project_root="/repo",
            verbose=False,
            save=True,
            commit_sha="abcdef123456",
        )

    assert res == "Batch review output."
    # When save=True, only progress status is sent for tool call; no chunks sent
    assert mock_session.send_response.call_count == 1
    call1 = mock_session.send_response.call_args_list[0]
    assert call1[1]["result"]["status"] == "progress"
    assert "Commit abcdef1" in call1[1]["result"]["message"]
    assert "list_directory" in call1[1]["result"]["message"]


def test_review_session_interactive_dispatches_properly():
    session = ReviewSession("req_int", MagicMock())
    session.send_response = MagicMock()
    session._check_running = MagicMock(return_value=True)

    mock_client = MagicMock()
    mock_chat = MagicMock()
    mock_client.chats.create.return_value = mock_chat

    chunk = make_mock_chunk(text="Interactive review result")
    mock_chat.send_message_stream.return_value = [chunk]

    with (
        patch("vimini.agent.review.get_client", return_value=mock_client),
        patch("vimini.agent.review.temporary_git_worktree") as mock_wt,
    ):
        mock_wt.return_value.__enter__.return_value = "/repo"
        session._process_command("req_int", {"batch": False, "save": False, "prompt": "Hi"}, "c1")

    # Verify chunk was streamed and completed sent
    assert session.send_response.call_count == 2
    assert session.send_response.call_args_list[0][1]["result"]["status"] == "chunk"
    assert session.send_response.call_args_list[1][1]["result"]["status"] == "completed"


def test_review_session_batch_dispatches_when_save_or_batch_flag():
    session = ReviewSession("req_batch", MagicMock())
    session.send_response = MagicMock()
    session._handle_batch_review = MagicMock()
    session._handle_interactive_review = MagicMock()

    session._process_command("req_batch", {"batch": True}, "c1")
    session._handle_batch_review.assert_called_once_with("req_batch", {"batch": True}, "c1")
    session._handle_interactive_review.assert_not_called()

    session._handle_batch_review.reset_mock()
    session._process_command("req_batch", {"save": True}, "c1")
    session._handle_batch_review.assert_called_once_with("req_batch", {"save": True}, "c1")
    session._handle_interactive_review.assert_not_called()
