import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini import handler
from vimini import main
from vimini import chat
from vimini import code
from vimini import autocomplete
from vimini import commit


class MockVimDictionary:
    def __init__(self, initial=None):
        self._store = dict(initial or {})

    def get(self, key, default=None):
        return self._store.get(key, default)

    def __getitem__(self, key):
        return self._store[key]

    def __setitem__(self, key, value):
        self._store[key] = value


class MockBuffer(list):
    def __init__(self, initial=None, number=1, name=""):
        super().__init__(initial or [])
        self.number = number
        self.name = name
        self.options = {"modifiable": True}
        self.vars = MockVimDictionary()

    def append(self, item):
        if isinstance(item, list):
            self.extend(item)
        else:
            super().append(item)


def test_handler_registry():
    handler.clear_handlers()
    mock_handler = MagicMock()

    handler.register_handler("123", mock_handler)
    assert handler.get_handler("123") is mock_handler
    assert handler.get_handler(123) is mock_handler
    assert handler.get_handler("999") is None

    handler.unregister_handler("123")
    assert handler.get_handler("123") is None


def test_base_channel_handler_handle_error():
    h = handler.BaseChannelHandler("101")
    assert not h.is_finished()

    with patch("vimini.util.display_message") as mock_display:
        h.handle_error({"message": "Server failure"})
        assert h.is_finished()
        mock_display.assert_called_once_with("Error: Server failure", error=True)


def test_stream_buffer_handler_thought_and_chunk():
    buf = MockBuffer([], number=42, name="[42] Job [->G?]")
    buf.vars["vimini_job_id"] = "42"

    h = handler.StreamBufferHandler("42", buffer=buf)

    # Test thought
    h.handle_thought("Analyzing input...", verbose=True)
    assert any("Analyzing input..." in line for line in buf)
    assert "[<-G]" in buf.name

    # Test chunk
    h.handle_chunk("Streamed response chunk.")
    assert any("Streamed response chunk." in line for line in buf)
    # Spinner should have advanced to [<-\]
    assert "[<-\\]" in buf.name


def test_stream_buffer_handler_tool_request():
    buf = MockBuffer([], number=50, name="[50] Job")
    h = handler.StreamBufferHandler("50", buffer=buf)

    # With explicit text
    h.handle_tool_request({"text": "\n[Agent requested tool: read_file(foo.py)]\n"})
    assert any("[Agent requested tool: read_file(foo.py)]" in line for line in buf)

    # With tool and args dict
    h.handle_tool_request({"tool": "list_directory", "args": {"directory_path": "."}})
    assert any("list_directory" in line for line in buf)


def test_main_handle_channel_message_dispatches_to_registered_handler():
    handler.clear_handlers()
    mock_handler = MagicMock()
    mock_handler.is_finished.return_value = False

    handler.register_handler("test_req", mock_handler)

    msg = {
        "jsonrpc": "2.0",
        "id": "test_req",
        "method": "review",
        "result": {"status": "chunk", "text": "Hello world"},
    }

    main.handle_channel_message(msg)

    mock_handler.handle_response.assert_called_once_with({"status": "chunk", "text": "Hello world"})
    # Handler was not finished, so remains registered
    assert handler.get_handler("test_req") is mock_handler


def test_main_handle_channel_message_unregisters_finished_handler():
    handler.clear_handlers()
    mock_handler = MagicMock()
    mock_handler.is_finished.return_value = True

    handler.register_handler("done_req", mock_handler)

    msg = {
        "jsonrpc": "2.0",
        "id": "done_req",
        "result": {"status": "completed"},
    }

    main.handle_channel_message(msg)

    mock_handler.handle_response.assert_called_once_with({"status": "completed"})
    # Finished handler should be unregistered automatically
    assert handler.get_handler("done_req") is None


def test_main_handle_channel_message_error_dispatches_to_handler():
    handler.clear_handlers()
    mock_handler = MagicMock()
    mock_handler.is_finished.return_value = True

    handler.register_handler("err_req", mock_handler)
    msg = {"jsonrpc": "2.0", "id": "err_req", "error": {"message": "Bad request"}}
    main.handle_channel_message(msg)

    mock_handler.handle_error.assert_called_once_with({"message": "Bad request"})


def test_chat_channel_handler_turn_persists():
    handler.clear_handlers()
    import vim

    buf = MockBuffer([], number=60, name="[60] Vimini Chat")
    buf.vars["vimini_job_id"] = "60"

    h = chat.ChatChannelHandler("60", buffer=buf)
    handler.register_handler("60", h)

    with patch.object(vim, "buffers", [buf]):
        # Send first turn chunk
        main.handle_channel_message({
            "id": "60",
            "result": {"status": "chunk", "text": "Turn 1 answer"},
        })
        assert any("Turn 1 answer" in line for line in buf)

        # Send done: chat handler must NOT be finished (multi-turn conversation)
        main.handle_channel_message({
            "id": "60",
            "result": {"status": "done"},
        })
        assert not h.is_finished()
        assert handler.get_handler("60") is h
        assert any(chat.WAITING_MSG in line for line in buf)

        # Send second turn chunk
        main.handle_channel_message({
            "id": "60",
            "result": {"status": "chunk", "text": "Turn 2 answer"},
        })
        assert any("Turn 2 answer" in line for line in buf)

        # Send terminated: handler is now finished
        main.handle_channel_message({
            "id": "60",
            "result": {"status": "terminated"},
        })
        assert h.is_finished()
        assert handler.get_handler("60") is None


def test_code_channel_handler_flow():
    handler.clear_handlers()
    import vim

    buf = MockBuffer([], number=70, name="[70] Vimini Code [->G]")
    buf.vars["vimini_job_id"] = "70"

    h = code.CodeChannelHandler("70", buffer=buf)
    handler.register_handler("70", h)

    with (
        patch.object(vim, "buffers", [buf]),
        patch.object(vim, "command") as mock_cmd,
        patch("vim.eval", return_value="on"),
    ):
        # Send thought
        main.handle_channel_message({
            "id": "70",
            "result": {"status": "thought", "thought": "Refactoring code...", "verbose": True},
        })
        assert any("Refactoring code..." in line for line in buf)
        assert "[<-G]" in buf.name

        # Send chunk
        main.handle_channel_message({
            "id": "70",
            "result": {"status": "chunk", "text": "{\"diff\": 1}"},
        })
        assert h.stream_json == "{\"diff\": 1}"

        # Send completed
        main.handle_channel_message({
            "id": "70",
            "result": {
                "status": "completed",
                "files": ["main.py"],
                "diff_output": "--- a/main.py\n+++ b/main.py\n@@ -1 +1 @@\n-old\n+new",
            },
        })
        assert h.is_finished()
        assert handler.get_handler("70") is None
        assert buf.name == "[70] Vimini Code"
        assert any(code._DIFF_SEPARATOR in line for line in buf)
        assert any("+new" in line for line in buf)
        assert 70 in code._BUFFER_DATA_STORE
        assert code._BUFFER_DATA_STORE[70]["files_to_apply"] == ["main.py"]

        # Clean up store
        del code._BUFFER_DATA_STORE[70]


def test_autocomplete_channel_handler_flow():
    handler.clear_handlers()
    autocomplete.cancel_autocomplete()

    h = autocomplete.AutocompleteChannelHandler("auto_1")
    handler.register_handler("auto_1", h)

    with patch("vimini.autocomplete._show_autocomplete_popup") as mock_popup:
        main.handle_channel_message({
            "id": "auto_1",
            "result": {"text": "return True\nmore"},
        })
        mock_popup.assert_called_once_with("return True")
        assert h.is_finished()
        assert handler.get_handler("auto_1") is None


def test_autocomplete_channel_handler_error():
    handler.clear_handlers()
    h = autocomplete.AutocompleteChannelHandler("auto_2")
    handler.register_handler("auto_2", h)

    with patch("vimini.util.log_info") as mock_log:
        main.handle_channel_message({"id": "auto_2", "error": {"message": "Cancelled"}})
        assert h.is_finished()
        assert handler.get_handler("auto_2") is None
        assert any("Cancelled" in str(c) for c in mock_log.call_args_list)


def test_setup_channel_handler_flow():
    handler.clear_handlers()
    h = main.SetupChannelHandler("setup")
    handler.register_handler("setup", h)

    with patch("vimini.util.log_info") as mock_log:
        main.handle_channel_message({"id": "setup", "result": {"status": "ok"}})
        assert h.is_finished()
        assert handler.get_handler("setup") is None
        assert any("setup completed" in str(c) for c in mock_log.call_args_list)


def test_list_models_channel_handler_flow():
    handler.clear_handlers()
    h = main.ListModelsChannelHandler("list_models")
    handler.register_handler("list_models", h)

    with patch("vimini.models.show_models_list") as mock_show:
        main.handle_channel_message({
            "id": "list_models",
            "result": {"status": "ok", "models": [{"name": "gemini-flash"}]},
        })
        mock_show.assert_called_once_with([{"name": "gemini-flash"}])
        assert h.is_finished()
        assert handler.get_handler("list_models") is None


def test_commit_channel_handler_flow():
    handler.clear_handlers()
    h = commit.CommitChannelHandler("commit_1")
    handler.register_handler("commit_1", h)

    with patch("vimini.commit.handle_commit_response") as mock_commit_resp:
        main.handle_channel_message({
            "id": "commit_1",
            "result": {
                "status": "ok",
                "text": "feat: new feature",
            },
        })
        mock_commit_resp.assert_called_once()
        assert h.is_finished()
        assert handler.get_handler("commit_1") is None
