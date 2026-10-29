import os
import sys
from unittest.mock import patch

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini.agent import server


def test_setup_strips_api_key_fields_and_reloads_global_config():
    server.AGENT_CONFIG.clear()

    class DummyQueue:
        def __init__(self):
            self.items = []

        def put(self, item):
            self.items.append(item)

    queue = DummyQueue()
    params = {
        "backend": "Gemini",
        "model": "gemini-3.8-flash",
        "api_key_file": "~/.config/gemini.token",
        "api_key": "secret",
    }

    with patch.object(server, "load_global_config") as mock_load:
        server.execute_function("setup", "setup", params, queue, object())
        mock_load.assert_called_once()

    assert server.AGENT_CONFIG == {
        "backend": "Gemini",
        "model": "gemini-3.8-flash",
    }
    assert queue.items[0][1][1]["result"] == {"status": "ok"}
