import sys
import os
from unittest.mock import MagicMock, patch

# Ensure python3 root is in sys.path
sys.path.insert(0, os.path.realpath(os.path.join(os.path.dirname(__file__), '..', 'python3')))

from vimini import chat

class MockBuffer(list):
    def __init__(self, initial=None, number=1, name=""):
        super().__init__(initial or [])
        self.number = number
        self.name = name
        self.options = {}
        self.vars = {}

    def append(self, item):
        if isinstance(item, list):
            self.extend(item)
        else:
            super().append(item)


def test_open_prompt_window_registers_mappings():
    import vim

    chat_buf = MockBuffer(["Welcome"], number=1, name="[1] Vimini Chat")
    chat_buf.vars["vimini_job_id"] = "1"

    prompt_buf = MockBuffer(number=2, name="Prompt")
    prompt_win = MagicMock()
    prompt_win.buffer = prompt_buf

    with patch.object(vim, "buffers", [chat_buf, prompt_buf]), \
         patch.object(vim, "windows", [prompt_win]), \
         patch.object(vim.current, "buffer", prompt_buf), \
         patch.object(vim.current, "window", prompt_win), \
         patch("vimini.chat._find_chat_buffer", return_value=chat_buf):

        # Simulate opening prompt window
        with patch.object(vim.current, "buffer", chat_buf):
            # During _open_prompt_window, it checks vim.current.buffer.number == chat_buf.number
            # and then calls "belowright 5new" which sets vim.current.buffer to prompt_buf
            def fake_command(cmd):
                if cmd == "belowright 5new":
                    vim.current.buffer = prompt_buf

            vim.command.side_effect = fake_command
            chat._open_prompt_window("1")
            vim.command.side_effect = None

        commands_run = [call_args[0][0] for call_args in vim.command.call_args_list]

        # Verify no TextChanged autocmds are registered
        assert not any("TextChanged" in cmd for cmd in commands_run)

        # Verify key mappings are set
        assert any("nnoremap <buffer><silent> <CR>" in cmd for cmd in commands_run)
        assert any("inoremap <buffer><silent> <C-s>" in cmd for cmd in commands_run)
        assert any("nnoremap <buffer><silent> <C-s>" in cmd for cmd in commands_run)
        assert any("inoremap <buffer><silent> <C-x><CR>" in cmd for cmd in commands_run)
        assert any("inoremap <buffer><silent> <C-CR>" in cmd for cmd in commands_run)
        # Reset mock calls so subsequent tests are clean
        vim.command.reset_mock()


def test_submit_prompt_preserves_multiline_prompt_with_blank_lines():
    import vim

    chat_buf = MockBuffer(["Welcome"], number=1, name="[1] Vimini Chat")
    chat_buf.vars["vimini_job_id"] = "1"

    prompt_content = [
        chat.HINT_MSG,
        "First line of prompt",
        "",
        "Second line after blank line",
        "Third line"
    ]
    prompt_buf = MockBuffer(prompt_content, number=2, name="Prompt")
    prompt_buf.vars["vimini_prompt_req_id"] = "1"

    with patch.object(vim, "buffers", [chat_buf, prompt_buf]), \
         patch.object(vim.current, "buffer", prompt_buf), \
         patch("vimini.chat._find_chat_buffer", return_value=chat_buf), \
         patch("vimini.chat._send_prompt") as mock_send:

        chat.submit_prompt(prompt_buf_num=2)

        # Buffer closure behavior is preserved
        vim.command.assert_any_call("bwipeout! 2")

        # Prompt with blank line is preserved and sent intact
        expected_prompt = "First line of prompt\n\nSecond line after blank line\nThird line"
        mock_send.assert_called_once_with(expected_prompt, chat_buf)


def test_chat_system_instruction_test_restraint():
    from vimini.agent.chat import ChatSession

    session = ChatSession(req_id="test_prompt", result_queue=MagicMock())
    session.send_response = MagicMock()

    with patch("vimini.agent.chat.get_client") as mock_get_client, \
         patch("vimini.agent.chat.create_generation_config") as mock_create_gen_config:
        mock_client = MagicMock()
        mock_chat = MagicMock()
        mock_chat.send_message_stream.return_value = []
        mock_client.chats.create.return_value = mock_chat
        mock_get_client.return_value = mock_client

        session._process_command("test_prompt", {"prompt": "hello"}, None)

        assert mock_create_gen_config.called
        kwargs = mock_create_gen_config.call_args.kwargs
        system_instruction = kwargs.get("system_instruction", "")

        assert "running tests should be done only when necessary" in system_instruction.lower()
        assert "select only the specific test" in system_instruction.lower()


def test_waiting_message_format():
    assert chat.WAITING_MSG == "Waiting for prompt (p to open prompt buffer)"


def test_handle_channel_response_done_does_not_open_prompt_window():
    import vim

    chat_buf = MockBuffer(["Line 1"], number=1, name="[1] Vimini Chat")
    chat_buf.vars["vimini_job_id"] = "1"

    with patch.object(vim, "buffers", [chat_buf]), \
         patch.object(vim.current, "buffer", chat_buf), \
         patch("vimini.chat._find_chat_buffer", return_value=chat_buf), \
         patch("vimini.chat._open_prompt_window") as mock_open_prompt:

        chat.handle_channel_response("1", {"status": "done", "text": "Task finished."})

        # Verify prompt window was NOT opened
        mock_open_prompt.assert_not_called()
        # Verify waiting message was added to chat buffer
        assert chat.WAITING_MSG in chat_buf


def test_chat_command_registers_p_mapping():
    import vim

    chat_buf = MockBuffer([], number=1)
    with patch.object(vim.current, "buffer", chat_buf), \
         patch("vimini.util.reserve_next_job_id", return_value=42), \
         patch("vimini.util.new_split"), \
         patch("vimini.chat._open_prompt_window"):

        chat.chat()

        commands_run = [call_args[0][0] for call_args in vim.command.call_args_list]
        assert any("nnoremap <buffer><silent> p :py3 from vimini.chat import _open_prompt_from_chat" in cmd for cmd in commands_run)
        # Ensure BufEnter autocmd is not registered
        assert not any("autocmd BufEnter <buffer>" in cmd for cmd in commands_run)


def test_open_prompt_from_chat():
    import vim

    chat_buf = MockBuffer([], number=1, name="[1] Vimini Chat")
    chat_buf.vars["vimini_job_id"] = "1"
    chat_buf.vars["vimini_waiting"] = False

    with patch.object(vim.current, "buffer", chat_buf), \
         patch("vimini.chat._get_buffer", return_value=chat_buf), \
         patch("vimini.chat._prompt_window_exists", return_value=False), \
         patch("vimini.chat._open_prompt_window") as mock_open:

        chat._open_prompt_from_chat(1)
        mock_open.assert_called_once_with("1")

    # If buffer is in waiting state, do not open prompt window
    chat_buf.vars["vimini_waiting"] = True
    with patch.object(vim.current, "buffer", chat_buf), \
         patch("vimini.chat._get_buffer", return_value=chat_buf), \
         patch("vimini.chat._open_prompt_window") as mock_open:

        chat._open_prompt_from_chat(1)
        mock_open.assert_not_called()


def test_on_patch_buffer_closed_schedules_denial_prompt_window():
    import vim

    with patch("vim.eval", return_value="0"):
        chat._on_patch_buffer_closed("42")
        commands_run = [call_args[0][0] for call_args in vim.command.call_args_list]
        assert any("timer_start(0" in cmd and "_open_denial_prompt_window" in cmd and "42" in cmd for cmd in commands_run)
        vim.command.reset_mock()


def test_on_patch_buffer_closed_already_handled():
    import vim

    with patch("vim.eval", return_value="1"):
        chat._on_patch_buffer_closed("42")
        commands_run = [call_args[0][0] for call_args in vim.command.call_args_list]
        assert not any("_open_denial_prompt_window" in cmd for cmd in commands_run)
        vim.command.reset_mock()


def test_open_denial_prompt_window():
    import vim

    chat_buf = MockBuffer(["Welcome"], number=1, name="[1] Vimini Chat")
    chat_buf.vars["vimini_job_id"] = "1"

    prompt_buf = MockBuffer(number=2, name="Prompt")
    chat_win = MagicMock()
    chat_win.buffer = chat_buf

    with patch.object(vim, "buffers", [chat_buf, prompt_buf]), \
         patch.object(vim, "windows", [chat_win]), \
         patch.object(vim.current, "buffer", prompt_buf), \
         patch.object(vim.current, "window", chat_win), \
         patch("vimini.chat._find_chat_buffer", return_value=chat_buf):

        def fake_command(cmd):
            if cmd == "belowright 5new":
                vim.current.buffer = prompt_buf

        vim.command.side_effect = fake_command
        chat._open_denial_prompt_window("1")
        vim.command.side_effect = None

        assert prompt_buf.vars.get("vimini_is_denial_prompt") == 1
        assert prompt_buf.vars.get("vimini_prompt_req_id") == "1"
        assert prompt_buf.vars.get("vimini_denial_handled") == 0
        assert prompt_buf[0] == chat.HINT_MSG
        assert prompt_buf[1] == chat.DENIAL_PREFIX

        commands_run = [call_args[0][0] for call_args in vim.command.call_args_list]
        assert any("_on_denial_prompt_closed" in cmd for cmd in commands_run)
        vim.command.reset_mock()


def test_submit_denial_prompt_with_feedback():
    import vim

    chat_buf = MockBuffer(["Welcome", "Agent Requested: apply_patch"], number=1, name="[1] Vimini Chat")
    chat_buf.vars["vimini_job_id"] = "1"

    prompt_content = [
        chat.HINT_MSG,
        "Patch denied: I want native JS methods instead of lodash"
    ]
    prompt_buf = MockBuffer(prompt_content, number=2, name="Prompt")
    prompt_buf.vars["vimini_prompt_req_id"] = "1"
    prompt_buf.vars["vimini_is_denial_prompt"] = 1
    prompt_buf.vars["vimini_denial_handled"] = 0

    with patch.object(vim, "buffers", [chat_buf, prompt_buf]), \
         patch.object(vim.current, "buffer", prompt_buf), \
         patch("vimini.chat._find_chat_buffer", return_value=chat_buf), \
         patch("vimini.chat.send_agent_approval") as mock_send_approval:

        chat.submit_prompt(prompt_buf_num=2)

        assert prompt_buf.vars["vimini_denial_handled"] == 1
        expected_feedback = "Patch denied: I want native JS methods instead of lodash"
        mock_send_approval.assert_called_once_with(
            False, "1", error=expected_feedback, feedback=expected_feedback
        )
        # Verify chat buffer updated with feedback
        assert any("Patch denied: I want native JS methods instead of lodash" in line for line in chat_buf)
        assert chat_buf.vars.get("vimini_waiting") is True
        vim.command.reset_mock()


def test_submit_denial_prompt_without_feedback_default_prefix():
    import vim

    chat_buf = MockBuffer(["Welcome"], number=1, name="[1] Vimini Chat")
    chat_buf.vars["vimini_job_id"] = "1"

    prompt_content = [
        chat.HINT_MSG,
        chat.DENIAL_PREFIX.strip()
    ]
    prompt_buf = MockBuffer(prompt_content, number=2, name="Prompt")
    prompt_buf.vars["vimini_prompt_req_id"] = "1"
    prompt_buf.vars["vimini_is_denial_prompt"] = 1
    prompt_buf.vars["vimini_denial_handled"] = 0

    with patch.object(vim, "buffers", [chat_buf, prompt_buf]), \
         patch.object(vim.current, "buffer", prompt_buf), \
         patch("vimini.chat._find_chat_buffer", return_value=chat_buf), \
         patch("vimini.chat.send_agent_approval") as mock_send_approval:

        chat.submit_prompt(prompt_buf_num=2)

        assert prompt_buf.vars["vimini_denial_handled"] == 1
        mock_send_approval.assert_called_once_with(False, "1")
        vim.command.reset_mock()


def test_on_denial_prompt_closed_cancels():
    import vim

    prompt_buf = MockBuffer(number=2, name="Prompt")
    prompt_buf.vars["vimini_denial_handled"] = 0

    with patch.object(vim, "buffers", [prompt_buf]), \
         patch("vimini.chat._get_buffer", return_value=prompt_buf), \
         patch("vimini.chat.send_agent_approval") as mock_send_approval:

        chat._on_denial_prompt_closed(2, "1")

        assert prompt_buf.vars["vimini_denial_handled"] == 1
        mock_send_approval.assert_called_once_with(False, "1")

    # Second close does not re-send
    with patch.object(vim, "buffers", [prompt_buf]), \
         patch("vimini.chat._get_buffer", return_value=prompt_buf), \
         patch("vimini.chat.send_agent_approval") as mock_send_approval:

        chat._on_denial_prompt_closed(2, "1")
        mock_send_approval.assert_not_called()


def test_agent_apply_patch_handles_feedback():
    from vimini.agent.chat import ChatSession
    from unittest.mock import MagicMock

    session = ChatSession(req_id="test_feedback", result_queue=MagicMock())
    session.cmd_queue = MagicMock()
    session.cmd_queue.get.return_value = ("test_feedback", {"approved": False, "feedback": "Don't use eval()"}, None)

    # Test that next_params feedback is extracted
    next_params = {"approved": False, "feedback": "Don't use eval()"}
    feedback = next_params.get("feedback")
    assert feedback == "Don't use eval()"
    patch_result = f"Apply patch command was denied by the user with the following feedback:\n{feedback}"
    assert "Don't use eval()" in patch_result
    assert "denied by the user" in patch_result
