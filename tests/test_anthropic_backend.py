import io
import json
import os
import sys
import pytest
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from google.genai import types
from vimini.common.backends.base import ToolCall, ToolResponse
from vimini.common.backends.anthropic_backend import (
    AnthropicBackend,
    AnthropicChatSession,
    convert_schema_to_anthropic,
    convert_tools_to_anthropic,
)
from vimini.common.backends import get_backend


def test_convert_schema_to_anthropic():
    schema = types.Schema(
        type=types.Type.OBJECT,
        description="A test object",
        properties={
            "path": types.Schema(type=types.Type.STRING, description="File path"),
            "count": types.Schema(type=types.Type.INTEGER, enum=["1", "2"]),
        },
        required=["path"],
    )

    converted = convert_schema_to_anthropic(schema)
    assert converted["type"] == "object"
    assert converted["description"] == "A test object"
    assert "path" in converted["properties"]
    assert converted["properties"]["path"]["type"] == "string"
    assert converted["properties"]["path"]["description"] == "File path"
    assert converted["properties"]["count"]["type"] == "integer"
    assert converted["properties"]["count"]["enum"] == ["1", "2"]
    assert converted["required"] == ["path"]


def test_convert_schema_none_and_dict():
    assert convert_schema_to_anthropic(None) == {"type": "object", "properties": {}}

    dict_schema = {"type": "STRING", "description": "test"}
    conv = convert_schema_to_anthropic(dict_schema)
    assert conv["type"] == "string"
    assert conv["description"] == "test"


def test_convert_tools_to_anthropic():
    genai_tool = types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name="read_file",
                description="Reads file",
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={"filepath": types.Schema(type=types.Type.STRING)},
                    required=["filepath"],
                ),
            )
        ]
    )

    anthropic_tools = convert_tools_to_anthropic([genai_tool])
    assert len(anthropic_tools) == 1
    tool = anthropic_tools[0]
    assert tool["name"] == "read_file"
    assert tool["description"] == "Reads file"
    assert tool["input_schema"]["type"] == "object"
    assert "filepath" in tool["input_schema"]["properties"]


def test_convert_tools_dict_and_openai_format():
    dict_tool = {
        "type": "function",
        "function": {
            "name": "test_cmd",
            "description": "Runs test",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    tools = convert_tools_to_anthropic([dict_tool])
    assert len(tools) == 1
    assert tools[0]["name"] == "test_cmd"
    assert tools[0]["description"] == "Runs test"
    assert tools[0]["input_schema"]["type"] == "object"

    anthropic_native_tool = {
        "name": "direct_tool",
        "description": "Direct",
        "input_schema": {"type": "object"},
    }
    tools2 = convert_tools_to_anthropic([anthropic_native_tool])
    assert len(tools2) == 1
    assert tools2[0]["name"] == "direct_tool"
    assert tools2[0]["input_schema"] == {"type": "object"}


def test_anthropic_backend_init():
    backend = AnthropicBackend(
        config={
            "base_url": "https://api.anthropic.com",
            "headers": {"X-Custom": "custom-val"},
            "max_tokens": 8192,
            "anthropic_version": "2023-06-01",
        },
        api_key="sk-ant-test",
    )
    assert backend.base_url == "https://api.anthropic.com/v1"
    assert backend.api_key == "sk-ant-test"
    assert backend.headers == {"X-Custom": "custom-val"}
    assert backend.max_tokens == 8192
    assert backend.anthropic_version == "2023-06-01"


def test_anthropic_backend_make_request_headers():
    backend = AnthropicBackend(
        base_url="https://api.anthropic.com/v1",
        api_key="secret-ant-key",
        headers={"X-Extra": "extra-val"},
    )

    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"status": "ok"}'

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        res = backend._make_request("/messages", data={"model": "claude-3-5-sonnet"})
        assert res == {"status": "ok"}

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://api.anthropic.com/v1/messages"
        assert req.headers["X-api-key"] == "secret-ant-key"
        assert req.headers["Anthropic-version"] == "2023-06-01"
        assert req.headers["X-extra"] == "extra-val"
        assert req.headers["Content-type"] == "application/json"


def test_anthropic_backend_make_request_error():
    backend = AnthropicBackend()
    with patch(
        "urllib.request.urlopen",
        side_effect=HTTPError(
            "url", 401, "Unauthorized", {}, io.BytesIO(b"Invalid API Key")
        ),
    ):
        with pytest.raises(
            RuntimeError, match="Anthropic HTTP error 401: Invalid API Key"
        ):
            backend._make_request("/messages")


def test_anthropic_backend_list_models():
    backend = AnthropicBackend()
    mock_data = {
        "data": [
            {
                "id": "claude-3-7-sonnet-20250219",
                "display_name": "Claude 3.7 Sonnet",
                "description": "Anthropic's latest hybrid reasoning model",
            },
            {
                "id": "claude-3-5-haiku-20241022",
                "display_name": "Claude 3.5 Haiku",
            },
        ]
    }

    with patch.object(backend, "_make_request", return_value=mock_data):
        models = backend.list_models()
        assert len(models) == 2
        assert models[0]["name"] == "claude-3-7-sonnet-20250219"
        assert models[0]["display_name"] == "Claude 3.7 Sonnet"
        assert models[0]["description"] == "Anthropic's latest hybrid reasoning model"
        assert models[0]["supported_actions"] == ["generateContent"]

        assert models[1]["name"] == "claude-3-5-haiku-20241022"
        assert models[1]["display_name"] == "Claude 3.5 Haiku"


def test_anthropic_backend_generate_content():
    backend = AnthropicBackend()
    mock_resp_data = {
        "content": [
            {
                "type": "text",
                "text": "Hello from Claude!",
            }
        ]
    }

    with patch.object(
        backend, "_make_request", return_value=mock_resp_data
    ) as mock_req:
        text = backend.generate_content(
            "Hello",
            model="claude-3-5-sonnet-20241022",
            system_instruction="Be helpful",
        )
        assert text == "Hello from Claude!"

        payload = mock_req.call_args[1]["data"]
        assert payload["model"] == "claude-3-5-sonnet-20241022"
        assert payload["system"] == "Be helpful"
        assert payload["messages"] == [{"role": "user", "content": "Hello"}]
        assert payload["stream"] is False


def test_anthropic_backend_generate_content_stream():
    backend = AnthropicBackend()

    sse_lines = [
        b'data: {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}}\n\n',
        b'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Thinking about it..."}}\n\n',
        b'data: {"type": "content_block_stop", "index": 0}\n\n',
        b'data: {"type": "content_block_start", "index": 1, "content_block": {"type": "text"}}\n\n',
        b'data: {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Hello"}}\n\n',
        b'data: {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": " world"}}\n\n',
        b'data: {"type": "content_block_stop", "index": 1}\n\n',
        b'data: {"type": "message_stop"}\n\n',
    ]
    mock_resp = io.BytesIO(b"".join(sse_lines))

    with patch.object(backend, "_make_request", return_value=mock_resp):
        chunks = list(
            backend.generate_content_stream("Hi", model="claude-3-5-sonnet-20241022")
        )

        assert len(chunks) == 3
        assert chunks[0].thought == "Thinking about it..."
        assert chunks[1].text == "Hello"
        assert chunks[2].text == " world"


def test_anthropic_read_sse_stream_error_event():
    backend = AnthropicBackend()
    sse_lines = [
        b'data: {"type": "error", "error": {"type": "overloaded_error", "message": "Server overloaded"}}\n\n'
    ]
    mock_resp = io.BytesIO(b"".join(sse_lines))

    with pytest.raises(
        RuntimeError, match="Anthropic streaming error: Server overloaded"
    ):
        list(backend._read_sse_stream(mock_resp))


def test_anthropic_chat_session_streaming_and_tools():
    backend = AnthropicBackend()
    session = backend.create_chat(
        model="claude-3-5-sonnet-20241022",
        system_instruction="System prompt",
        tools=[{"name": "read_file", "parameters": {}}],
    )

    assert isinstance(session, AnthropicChatSession)
    assert session.has_history() is False
    assert session.get_history() == []

    # Stream turn 1: text delta followed by tool_use block
    events_turn1 = [
        {"type": "message_start", "message": {"id": "msg_1", "role": "assistant"}},
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "Let me read the file."},
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_01",
                "name": "read_file",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {
                "type": "input_json_delta",
                "partial_json": '{"filepath": "te',
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {"type": "input_json_delta", "partial_json": 'st.py"}'},
        },
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"},
    ]

    sse_lines_1 = [f"data: {json.dumps(ev)}\n\n".encode("utf-8") for ev in events_turn1]
    mock_resp1 = io.BytesIO(b"".join(sse_lines_1))

    with patch.object(backend, "_make_request", return_value=mock_resp1):
        chunks = list(session.send_message_stream("Read test.py"))

        assert len(chunks) == 2
        assert chunks[0].text == "Let me read the file."
        assert len(chunks[1].tool_calls) == 1
        tc = chunks[1].tool_calls[0]
        assert tc.name == "read_file"
        assert tc.args == {"filepath": "test.py"}
        assert tc.call_id == "toolu_01"

    # Turn 2: tool response
    events_turn2 = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {"type": "text", "text": ""},
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {"type": "text_delta", "text": "The file content is valid."},
        },
        {"type": "content_block_stop", "index": 0},
        {"type": "message_stop"},
    ]
    sse_lines_2 = [f"data: {json.dumps(ev)}\n\n".encode("utf-8") for ev in events_turn2]
    mock_resp2 = io.BytesIO(b"".join(sse_lines_2))

    tool_res = ToolResponse(
        name="read_file", response="print('hello')", call_id="toolu_01"
    )

    with patch.object(backend, "_make_request", return_value=mock_resp2) as mock_req2:
        chunks2 = list(session.send_message_stream([tool_res]))
        assert len(chunks2) == 1
        assert chunks2[0].text == "The file content is valid."

        call_payload = mock_req2.call_args[1]["data"]
        messages = call_payload["messages"]

        # Turn 1 user message
        assert messages[0]["role"] == "user"
        assert messages[0]["content"] == "Read test.py"

        # Turn 1 assistant message with text and tool_use blocks
        assert messages[1]["role"] == "assistant"
        assert len(messages[1]["content"]) == 2
        assert messages[1]["content"][0]["type"] == "text"
        assert messages[1]["content"][1]["type"] == "tool_use"
        assert messages[1]["content"][1]["id"] == "toolu_01"
        assert messages[1]["content"][1]["input"] == {"filepath": "test.py"}

        # Turn 2 user message containing tool_result block
        assert messages[2]["role"] == "user"
        assert len(messages[2]["content"]) == 1
        assert messages[2]["content"][0]["type"] == "tool_result"
        assert messages[2]["content"][0]["tool_use_id"] == "toolu_01"
        assert messages[2]["content"][0]["content"] == "print('hello')"


def test_anthropic_chat_session_multiple_tool_calls():
    backend = AnthropicBackend()
    session = backend.create_chat(
        model="claude-3-5-sonnet-20241022",
        tools=[
            {"name": "read_file", "parameters": {}},
            {"name": "git_grep", "parameters": {}},
        ],
    )

    events_turn = [
        {
            "type": "content_block_start",
            "index": 0,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_a",
                "name": "read_file",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 0,
            "delta": {
                "type": "input_json_delta",
                "partial_json": '{"filepath": "a.txt"}',
            },
        },
        {"type": "content_block_stop", "index": 0},
        {
            "type": "content_block_start",
            "index": 1,
            "content_block": {
                "type": "tool_use",
                "id": "toolu_b",
                "name": "git_grep",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "index": 1,
            "delta": {
                "type": "input_json_delta",
                "partial_json": '{"pattern": "TODO"}',
            },
        },
        {"type": "content_block_stop", "index": 1},
        {"type": "message_stop"},
    ]

    mock_resp = io.BytesIO(
        b"".join([f"data: {json.dumps(ev)}\n\n".encode("utf-8") for ev in events_turn])
    )

    with patch.object(backend, "_make_request", return_value=mock_resp):
        chunks = list(session.send_message_stream("Check both"))

        assert len(chunks) == 1
        assert len(chunks[0].tool_calls) == 2
        assert chunks[0].tool_calls[0].name == "read_file"
        assert chunks[0].tool_calls[1].name == "git_grep"

    # Multi-tool response in single user message
    resp_sse = io.BytesIO(
        b'data: {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "All done"}}\n\ndata: {"type": "message_stop"}\n\n'
    )
    with patch.object(backend, "_make_request", return_value=resp_sse) as mock_req:
        list(
            session.send_message_stream(
                [
                    ToolResponse(
                        name="read_file", response="content_a", call_id="toolu_a"
                    ),
                    ToolResponse(
                        name="git_grep", response="content_b", call_id="toolu_b"
                    ),
                ]
            )
        )

        messages = mock_req.call_args[1]["data"]["messages"]
        user_msg = messages[2]
        assert user_msg["role"] == "user"
        assert len(user_msg["content"]) == 2
        assert user_msg["content"][0]["tool_use_id"] == "toolu_a"
        assert user_msg["content"][1]["tool_use_id"] == "toolu_b"


def test_anthropic_chat_session_consecutive_user_prompts_merged():
    backend = AnthropicBackend()
    session = backend.create_chat(model="claude-3-5-sonnet")

    resp1 = io.BytesIO(b'data: {"type": "message_stop"}\n\n')
    with patch.object(backend, "_make_request", return_value=resp1):
        list(session.send_message_stream("first message"))

    resp2 = io.BytesIO(b'data: {"type": "message_stop"}\n\n')
    with patch.object(backend, "_make_request", return_value=resp2) as mock_req:
        list(session.send_message_stream("second message"))

        messages = mock_req.call_args[1]["data"]["messages"]
        # Merged into single user turn
        assert len(messages) == 1
        assert messages[0]["content"] == "first message\n\nsecond message"


def test_get_backend_factory_resolves_anthropic_backend():
    config = {
        "active_backend": "Claude",
        "backends": {
            "Claude": {
                "api": "anthropic",
                "api_key_file": "~/.config/anthropic.token",
                "default_model": "claude-3-7-sonnet-20250219",
            }
        },
    }

    with patch(
        "vimini.common.backends.load_backend_api_key", return_value="fake-ant-key"
    ):
        backend = get_backend("Claude", config=config)
    assert isinstance(backend, AnthropicBackend)
    assert backend.base_url == "https://api.anthropic.com/v1"
    assert backend.api_key == "fake-ant-key"


def test_anthropic_backend_missing_api_key_raises():
    config = {
        "active_backend": "Claude",
        "backends": {
            "Claude": {
                "api": "anthropic",
                "api_key_file": "~/.config/missing.token",
                "default_model": "claude-3-7-sonnet-20250219",
            }
        },
    }

    with patch("vimini.common.backends.load_backend_api_key", return_value=None):
        with pytest.raises(RuntimeError, match="API key for backend 'Claude'"):
            get_backend("Claude", config=config)


def test_anthropic_backend_keyless_allowed():
    config = {
        "active_backend": "LocalClaude",
        "backends": {
            "LocalClaude": {
                "api": "anthropic",
                "requires_api_key": False,
                "base_url": "http://localhost:8000/v1",
                "default_model": "claude-local",
            }
        },
    }

    backend = get_backend("LocalClaude", config=config)
    assert isinstance(backend, AnthropicBackend)
    assert backend.api_key is None
    assert backend.base_url == "http://localhost:8000/v1"
