import os
import sys
import queue
import pytest
from unittest.mock import MagicMock, patch

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini.common.backends.base import (
    BaseChatSession,
    BaseClient,
    StreamChunk,
    ToolCall,
)
from vimini.agent.server import execute_function
from vimini.agent.chat import ChatSession


class DummyProxyChatSession(BaseChatSession):
    def __init__(self):
        self._history_queried = False

    def send_message_stream(self, prompt_or_tool_responses):
        yield StreamChunk(text="Response from ProxyAPI")

    def has_history(self):
        return False

    def get_history(self):
        self._history_queried = True
        return []


class DummyProxyClient(BaseClient):
    def __init__(self):
        self.chat_session = DummyProxyChatSession()

    def list_models(self):
        return [
            {
                "name": "gpt-4o",
                "display_name": "GPT-4o",
                "description": "",
                "supported_actions": ["generateContent"],
            }
        ]

    def generate_content(self, prompt, model, system_instruction=None, config=None):
        return f"Generated for {prompt}"

    def generate_content_stream(
        self,
        prompt,
        model,
        system_instruction=None,
        verbose=False,
        response_schema=None,
        config=None,
    ):
        yield StreamChunk(text='{"files": []}')

    def create_chat(
        self,
        model,
        system_instruction=None,
        tools=None,
        history=None,
        verbose=False,
        config=None,
    ):
        return self.chat_session


def test_server_execute_function_uses_get_client():
    dummy_client = DummyProxyClient()
    result_q = queue.Queue()

    with patch("vimini.agent.server.get_client", return_value=dummy_client):
        # 1. list_models
        execute_function("req1", "list_models", {}, result_q, "conn1")
        conn, resp = result_q.get()
        assert resp[1]["result"]["status"] == "ok"
        assert resp[1]["result"]["models"][0]["name"] == "gpt-4o"

        # 2. commit
        execute_function("req2", "commit", {"prompt": "Commit msg"}, result_q, "conn1")
        conn, resp = result_q.get()
        assert resp[1]["result"]["status"] == "ok"
        assert resp[1]["result"]["text"] == "Generated for Commit msg"

        # 3. autocomplete
        execute_function(
            "req3", "autocomplete", {"prompt": "def func():"}, result_q, "conn1"
        )
        conn, resp = result_q.get()
        assert resp[1]["result"]["status"] == "ok"
        assert resp[1]["result"]["text"] == "Generated for def func():"


def test_chat_session_honors_has_history_false():
    dummy_client = DummyProxyClient()
    result_q = queue.Queue()

    session = ChatSession(
        req_id="chat1",
        result_queue=result_q,
        agent_config={"backend": "Custom 1", "model": "gpt-4o"},
    )
    session.send_response = MagicMock()

    with patch("vimini.agent.chat.get_client", return_value=dummy_client):
        session._process_command("chat1", {"prompt": "Hello"}, "mock_conn")

        # ProxyChatSession has has_history() == False
        assert session.chat_history is None
        # get_history() should not have been called
        assert dummy_client.chat_session._history_queried is False

        # Verify streamed response
        chunk_calls = [
            call
            for call in session.send_response.call_args_list
            if call[1].get("result", {}).get("status") == "chunk"
        ]
        assert len(chunk_calls) == 1
        assert chunk_calls[0][1]["result"]["text"] == "Response from ProxyAPI"
