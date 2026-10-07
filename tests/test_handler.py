import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini import handler
from vimini import main


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
