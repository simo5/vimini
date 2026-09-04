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

        commands_run = [call_args[0][0] for call_args in vim.command.call_args_list]

        # Verify no TextChanged autocmds are registered
        assert not any("TextChanged" in cmd for cmd in commands_run)

        # Verify key mappings are set
        assert any("nnoremap <buffer><silent> <CR>" in cmd for cmd in commands_run)
        assert any("inoremap <buffer><silent> <C-s>" in cmd for cmd in commands_run)
        assert any("nnoremap <buffer><silent> <C-s>" in cmd for cmd in commands_run)
        assert any("inoremap <buffer><silent> <C-x><CR>" in cmd for cmd in commands_run)
        assert any("inoremap <buffer><silent> <C-CR>" in cmd for cmd in commands_run)


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
