import sys
import os
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini import util
from vimini import review
from vimini import handler
from vimini import main


class MockVimDictionary:
    """Simulates vim.Dictionary which does not inherit from dict."""
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


def test_find_buffer_by_job_id_with_vim_dictionary():
    import vim

    buf1 = MockBuffer(["Hello"], number=1, name="[10] Vimini Review [->G?]")
    buf1.vars["vimini_job_id"] = "10"

    buf2 = MockBuffer(["World"], number=2, name="[11] Vimini Chat")
    buf2.vars["vimini_job_id"] = "11"

    with patch.object(vim, "buffers", [buf1, buf2]):
        found = util.find_buffer_by_job_id("10")
        assert found is buf1
        assert found.number == 1

        # Test bytes job_id
        found_bytes = util.find_buffer_by_job_id(b"11")
        assert found_bytes is buf2

        # Test non-existent
        assert util.find_buffer_by_job_id("99") is None


def test_find_buffer_by_job_id_fallback_name_hint():
    import vim

    # Buffer where vars["vimini_job_id"] was not set
    buf = MockBuffer([], number=5, name="[42] Vimini Review [->G?]")

    with patch.object(vim, "buffers", [buf]):
        found = util.find_buffer_by_job_id("42", name_hint="Vimini Review")
        assert found is buf


def test_write_to_buffer_appends_and_redraws():
    import vim

    buf = MockBuffer(["Initial line"], number=1)
    with patch.object(vim, "buffers", [buf]), patch.object(vim, "command") as mock_cmd:
        util.write_to_buffer(buf, "Appended line", append_to_last=False, redraw=True)
        assert "Appended line" in buf
        mock_cmd.assert_any_call("redraw")


def test_review_channel_handler_thought_and_chunk():
    import vim

    buf = MockBuffer([], number=10, name="[10] Vimini Review [->G?]")
    buf.vars["vimini_job_id"] = "10"

    h = review.ReviewChannelHandler("10", buffer=buf)
    handler.register_handler("10", h)

    with (
        patch.object(vim, "buffers", [buf]),
        patch.object(vim, "command") as mock_cmd,
        patch.object(vim, "eval", return_value="on"),
    ):
        # Test thought response via main.handle_channel_message dispatch
        main.handle_channel_message({
            "id": "10",
            "result": {"status": "thought", "thought": "Thinking about security...", "verbose": True},
        })
        assert any("Thinking about security..." in line for line in buf)
        assert "[<-G]" in buf.name

        # Test tool_use_requested response
        main.handle_channel_message({
            "id": "10",
            "result": {
                "status": "tool_use_requested",
                "tool": "read_file",
                "args": {"filepath": "foo.py"},
            },
        })
        assert any("read_file" in line for line in buf)

        # Test chunk response
        main.handle_channel_message({
            "id": "10",
            "result": {"status": "chunk", "text": "This is the review feedback."},
        })
        assert any("========== REVIEW START ==========" in line for line in buf)
        assert any("This is the review feedback." in line for line in buf)

        # Test completed response
        with patch("vimini.util.display_message") as mock_display:
            main.handle_channel_message({
                "id": "10",
                "result": {"status": "completed"},
            })
            mock_display.assert_called_once_with("Review completed.")
            assert buf.name == "[10] Vimini Review"
            # Finished handler should be unregistered
            assert handler.get_handler("10") is None


def test_review_channel_handler_error():
    import vim

    buf = MockBuffer([], number=11, name="[11] Vimini Review")
    h = review.ReviewChannelHandler("11", buffer=buf)
    handler.register_handler("11", h)

    with patch("vimini.util.display_message") as mock_display:
        main.handle_channel_message({
            "id": "11",
            "error": {"message": "Model timeout"},
        })
        assert any("Error: Model timeout" in line for line in buf)
        mock_display.assert_called_once_with("Error: Model timeout", error=True)
        assert handler.get_handler("11") is None


def test_review_assigns_job_id_to_non_dict_vars():
    import vim

    review_buf = MockBuffer(["def foo(): pass"], number=20, name="sample.py")
    # review_buf.vars is MockVimDictionary, which is NOT an instance of dict
    assert not isinstance(review_buf.vars, dict)

    with (
        patch.object(vim.current, "buffer", review_buf),
        patch.object(vim, "eval", return_value="python"),
        patch("vimini.util.reserve_next_job_id", return_value=99),
        patch("vimini.util.new_split"),
        patch("vim.command"),
        patch("vimini.util.send_channel_request"),
        patch("vim.eval", return_value="markdown"),
    ):
        review.review("Test prompt")

        # Verify vimini_job_id was successfully assigned to vars
        assert review_buf.vars.get("vimini_job_id") == "99"
