import sys
import os
import tempfile
import pytest
from unittest.mock import MagicMock, patch

# Ensure python3 root is in sys.path
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), '..', 'python3')))

from vimini.common.util import generate_diff_for_file
from vimini.agent.chat import get_agent_tools, validate_patch_is_safe, ChatSession


def test_generate_diff_for_modified_file(tmp_path):
    project_root = str(tmp_path)
    test_file = tmp_path / "hello.py"
    test_file.write_text("def hello():\n    print('hello')\n")

    new_content = "def hello():\n    print('hello world')\n"
    diff_text, err = generate_diff_for_file("hello.py", new_content, project_root=project_root)

    assert err is None
    assert diff_text is not None
    assert "diff --git a/hello.py b/hello.py" in diff_text
    assert "--- a/hello.py" in diff_text
    assert "+++ b/hello.py" in diff_text
    assert "-    print('hello')" in diff_text
    assert "+    print('hello world')" in diff_text


def test_generate_diff_for_new_file(tmp_path):
    project_root = str(tmp_path)
    new_content = "print('new file')\n"
    diff_text, err = generate_diff_for_file("new_module.py", new_content, project_root=project_root)

    assert err is None
    assert diff_text is not None
    assert "diff --git a/new_module.py b/new_module.py" in diff_text
    assert "--- /dev/null" in diff_text
    assert "+++ b/new_module.py" in diff_text
    assert "+print('new file')" in diff_text


def test_generate_diff_for_identical_file(tmp_path):
    project_root = str(tmp_path)
    test_file = tmp_path / "same.py"
    content = "a = 1\nb = 2\n"
    test_file.write_text(content)

    diff_text, err = generate_diff_for_file("same.py", content, project_root=project_root)
    assert err is None
    assert diff_text == ""


def test_generate_diff_path_traversal_security(tmp_path):
    project_root = str(tmp_path / "project")
    os.makedirs(project_root, exist_ok=True)

    diff_text, err = generate_diff_for_file("../../outside.py", "malicious", project_root=project_root)
    assert diff_text is None
    assert "Security error" in err


def test_generate_diff_target_is_directory(tmp_path):
    project_root = str(tmp_path)
    sub_dir = tmp_path / "somedir"
    sub_dir.mkdir()

    diff_text, err = generate_diff_for_file("somedir", "content", project_root=project_root)
    assert diff_text is None
    assert "directory" in err


def test_apply_patch_tool_declaration():
    tools = get_agent_tools()
    apply_patch_tool = None
    for t in tools:
        for fn in t.function_declarations:
            if fn.name == 'apply_patch':
                apply_patch_tool = fn
                break

    assert apply_patch_tool is not None
    props = apply_patch_tool.parameters.properties
    assert "file_path" in props
    assert "file_content" in props
    assert "diff_content" in props
    assert "entire file contents" in apply_patch_tool.description
    assert "locally" in apply_patch_tool.description


def test_chat_session_apply_patch_with_file_content(tmp_path):
    project_root = str(tmp_path)
    target_file = tmp_path / "test_script.py"
    target_file.write_text("def run():\n    pass\n")

    session = ChatSession(req_id="123", result_queue=MagicMock())
    session.project_root = project_root
    session.send_response = MagicMock()

    # Create mock tool call
    mock_tool_call = MagicMock()
    mock_tool_call.name = "apply_patch"
    mock_tool_call.args = {
        "file_path": "test_script.py",
        "file_content": "def run():\n    print('running')\n"
    }

    # Mock response stream containing the tool call
    mock_part = MagicMock()
    mock_part.function_call = mock_tool_call
    mock_part.thought = None
    mock_part.text = None

    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part]

    mock_chunk = MagicMock()
    mock_chunk.candidates = [mock_candidate]
    mock_chunk.text = None

    mock_client = MagicMock()
    mock_chat = MagicMock()
    # First send_message_stream returns chunk with tool call
    # Second send_message_stream returns final message
    final_chunk = MagicMock()
    final_chunk.candidates = []
    final_chunk.text = "Done updating file."

    mock_chat.send_message_stream.side_effect = [
        [mock_chunk],
        [final_chunk]
    ]
    mock_client.chats.create.return_value = mock_chat

    session.client = mock_client
    session.session = mock_chat

    # Put user approval into cmd_queue
    session.cmd_queue.put(("123", {"approved": True}, None))

    # Run _process_command
    with patch("os.remove") as mock_remove:
        session._process_command("123", {"prompt": "Update test_script.py", "project_root": project_root}, None)

    # Verify tool_use_requested was sent with the generated temp_file
    tool_requested_calls = [
        call for call in session.send_response.call_args_list
        if call.kwargs.get("result", {}).get("status") == "tool_use_requested"
    ]
    assert len(tool_requested_calls) == 1
    result_dict = tool_requested_calls[0].kwargs["result"]
    assert result_dict["tool"] == "apply_patch"
    assert result_dict["file_path"] == "test_script.py"
    temp_file = result_dict["temp_file"]

    # Verify the generated patch is valid
    is_safe, msg = validate_patch_is_safe(temp_file, project_root)
    assert is_safe is True

    # Clean up temp file if still present
    if os.path.exists(temp_file):
        os.remove(temp_file)


def test_chat_session_apply_patch_identical_content(tmp_path):
    project_root = str(tmp_path)
    target_file = tmp_path / "unchanged.py"
    content = "x = 10\n"
    target_file.write_text(content)

    session = ChatSession(req_id="456", result_queue=MagicMock())
    session.project_root = project_root
    session.send_response = MagicMock()

    diff_text, err = generate_diff_for_file("unchanged.py", content, project_root=project_root)
    assert err is None
    assert diff_text == ""


def test_generate_diff_empty_content_empties_file(tmp_path):
    project_root = str(tmp_path)
    target_file = tmp_path / "to_empty.txt"
    target_file.write_text("line 1\nline 2\n")

    diff_text, err = generate_diff_for_file("to_empty.txt", "", project_root=project_root)
    assert err is None
    assert diff_text is not None
    assert "--- a/to_empty.txt" in diff_text
    assert "+++ b/to_empty.txt" in diff_text
    assert "-line 1" in diff_text


def test_generate_diff_absolute_path_inside_project(tmp_path):
    project_root = str(tmp_path)
    target_file = tmp_path / "abs_test.py"
    target_file.write_text("val = 1\n")

    diff_text, err = generate_diff_for_file(str(target_file), "val = 2\n", project_root=project_root)
    assert err is None
    assert diff_text is not None
    assert "diff --git a/abs_test.py b/abs_test.py" in diff_text
    assert "+val = 2" in diff_text


def test_chat_session_apply_patch_with_multiple_files(tmp_path):
    project_root = str(tmp_path)
    file_a = tmp_path / "a.py"
    file_a.write_text("a = 1\n")
    file_b = tmp_path / "b.py"
    file_b.write_text("b = 1\n")

    session = ChatSession(req_id="789", result_queue=MagicMock())
    session.project_root = project_root
    session.send_response = MagicMock()

    mock_tool_call = MagicMock()
    mock_tool_call.name = "apply_patch"
    mock_tool_call.args = {
        "files": [
            {"file_path": "a.py", "file_content": "a = 2\n"},
            {"file_path": "b.py", "file_content": "b = 2\n"}
        ]
    }

    mock_part = MagicMock()
    mock_part.function_call = mock_tool_call
    mock_part.thought = None
    mock_part.text = None

    mock_chunk = MagicMock()
    mock_chunk.candidates = [MagicMock(content=MagicMock(parts=[mock_part]))]
    mock_chunk.text = None

    final_chunk = MagicMock(candidates=[], text="Done.")

    mock_client = MagicMock()
    mock_chat = MagicMock()
    mock_chat.send_message_stream.side_effect = [[mock_chunk], [final_chunk]]
    mock_client.chats.create.return_value = mock_chat
    session.client = mock_client
    session.session = mock_chat

    session.cmd_queue.put(("789", {"approved": True}, None))

    with patch("os.remove"):
        session._process_command("789", {"prompt": "Update both", "project_root": project_root}, None)

    tool_requested_calls = [
        call for call in session.send_response.call_args_list
        if call.kwargs.get("result", {}).get("status") == "tool_use_requested"
    ]
    assert len(tool_requested_calls) == 1
    result_dict = tool_requested_calls[0].kwargs["result"]
    assert "a.py, b.py" in result_dict["file_path"]
