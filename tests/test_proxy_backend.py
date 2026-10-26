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
from vimini.common.backends.base import StreamChunk, ToolCall, ToolResponse
from vimini.common.backends.proxy_backend import (
    ProxyBackend,
    ProxyChatSession,
    convert_schema_to_openai,
    convert_tools_to_openai,
)
from vimini.common.backends import get_backend


def test_convert_schema_to_openai():
    schema = types.Schema(
        type=types.Type.OBJECT,
        description="A test object",
        properties={
            "path": types.Schema(type=types.Type.STRING, description="File path"),
            "count": types.Schema(type=types.Type.INTEGER, enum=["1", "2"]),
        },
        required=["path"],
    )

    converted = convert_schema_to_openai(schema)
    assert converted["type"] == "object"
    assert converted["description"] == "A test object"
    assert "path" in converted["properties"]
    assert converted["properties"]["path"]["type"] == "string"
    assert converted["properties"]["path"]["description"] == "File path"
    assert converted["properties"]["count"]["type"] == "integer"
    assert converted["properties"]["count"]["enum"] == ["1", "2"]
    assert converted["required"] == ["path"]


def test_convert_schema_none_and_dict():
    assert convert_schema_to_openai(None) == {"type": "object", "properties": {}}

    dict_schema = {"type": "STRING", "description": "test"}
    conv = convert_schema_to_openai(dict_schema)
    assert conv["type"] == "string"
    assert conv["description"] == "test"


def test_convert_tools_to_openai():
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

    openai_tools = convert_tools_to_openai([genai_tool])
    assert len(openai_tools) == 1
    assert openai_tools[0]["type"] == "function"
    fn = openai_tools[0]["function"]
    assert fn["name"] == "read_file"
    assert fn["description"] == "Reads file"
    assert fn["parameters"]["type"] == "object"
    assert "filepath" in fn["parameters"]["properties"]


def test_convert_tools_dict():
    dict_tool = {
        "type": "function",
        "function": {"name": "test_cmd", "parameters": {}},
    }
    tools = convert_tools_to_openai([dict_tool])
    assert len(tools) == 1
    assert tools[0]["function"]["name"] == "test_cmd"


def test_proxy_backend_init():
    backend = ProxyBackend(
        config={
            "base_url": "https://custom.api/v1/",
            "headers": {"X-Test": "123"},
        },
        api_key="my-key",
    )
    assert backend.base_url == "https://custom.api/v1"
    assert backend.api_key == "my-key"
    assert backend.headers == {"X-Test": "123"}


def test_proxy_backend_make_request_headers():
    backend = ProxyBackend(
        base_url="https://api.openai.com/v1",
        api_key="secret",
        headers={"X-Custom": "val"},
    )

    mock_resp = MagicMock()
    mock_resp.read.return_value = b'{"status": "ok"}'

    with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
        res = backend._make_request("/test", data={"hello": "world"})
        assert res == {"status": "ok"}

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://api.openai.com/v1/test"
        assert req.headers["Authorization"] == "Bearer secret"
        assert req.headers["X-custom"] == "val"
        assert req.headers["Content-type"] == "application/json"


def test_proxy_backend_make_request_error():
    backend = ProxyBackend()
    with patch(
        "urllib.request.urlopen",
        side_effect=HTTPError(
            "url", 401, "Unauthorized", {}, io.BytesIO(b"Invalid API Key")
        ),
    ):
        with pytest.raises(
            RuntimeError, match="ProxyAPI HTTP error 401: Invalid API Key"
        ):
            backend._make_request("/test")


def test_proxy_backend_list_models():
    backend = ProxyBackend()
    mock_data = {
        "object": "list",
        "data": [
            {"id": "gpt-4o", "name": "GPT-4o", "description": "Smart model"},
            {"id": "o3-mini"},
        ],
    }

    with patch.object(backend, "_make_request", return_value=mock_data):
        models = backend.list_models()
        assert len(models) == 2
        assert models[0]["name"] == "gpt-4o"
        assert models[0]["display_name"] == "GPT-4o"
        assert models[0]["description"] == "Smart model"
        assert models[0]["supported_actions"] == ["generateContent"]

        assert models[1]["name"] == "o3-mini"
        assert models[1]["display_name"] == "o3-mini"


def test_proxy_backend_generate_content():
    backend = ProxyBackend()
    mock_resp_data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "This is generated content.",
                }
            }
        ]
    }

    with patch.object(backend, "_make_request", return_value=mock_resp_data):
        text = backend.generate_content(
            "Hello", model="gpt-4o", system_instruction="Be brief"
        )
        assert text == "This is generated content."


def test_proxy_backend_generate_content_stream():
    backend = ProxyBackend()

    sse_lines = [
        b'data: {"choices": [{"delta": {"reasoning_content": "Thinking..."}}]}\n\n',
        b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n',
        b'data: {"choices": [{"delta": {"content": " World"}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    mock_resp = io.BytesIO(b"".join(sse_lines))

    with patch.object(backend, "_make_request", return_value=mock_resp):
        chunks = list(backend.generate_content_stream("Hi", model="gpt-4o"))

        assert len(chunks) == 3
        assert chunks[0].thought == "Thinking..."
        assert chunks[1].text == "Hello"
        assert chunks[2].text == " World"


def test_proxy_chat_session_streaming_and_tools():
    backend = ProxyBackend()
    session = backend.create_chat(
        model="gpt-4o",
        system_instruction="System prompt",
        tools=[{"name": "read_file", "parameters": {}}],
    )

    assert isinstance(session, ProxyChatSession)
    assert session.has_history() is False
    assert session.get_history() == []

    chunk_text = {"choices": [{"delta": {"content": "Let me read the file."}}]}
    chunk_tc1 = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_1",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"file',
                            },
                        }
                    ]
                }
            }
        ]
    }
    chunk_tc2 = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {"arguments": 'path": "test.py"}'},
                        }
                    ]
                }
            }
        ]
    }

    sse_lines = [
        f"data: {json.dumps(chunk_text)}\n\n".encode("utf-8"),
        f"data: {json.dumps(chunk_tc1)}\n\n".encode("utf-8"),
        f"data: {json.dumps(chunk_tc2)}\n\n".encode("utf-8"),
        b"data: [DONE]\n\n",
    ]
    mock_resp = io.BytesIO(b"".join(sse_lines))

    with patch.object(backend, "_make_request", return_value=mock_resp):
        chunks = list(session.send_message_stream("Check file"))

        # Should yield text chunk first, then tool_calls chunk at stream end
        assert len(chunks) == 2
        assert chunks[0].text == "Let me read the file."
        assert len(chunks[1].tool_calls) == 1
        tc = chunks[1].tool_calls[0]
        assert tc.name == "read_file"
        assert tc.args == {"filepath": "test.py"}
        assert tc.call_id == "call_1"

    # Now verify tool response turn
    resp_sse_lines = [
        b'data: {"choices": [{"delta": {"content": "File content processed."}}]}\n\n',
        b"data: [DONE]\n\n",
    ]
    mock_resp2 = io.BytesIO(b"".join(resp_sse_lines))

    tool_res = ToolResponse(
        name="read_file", response="file content here", call_id="call_1"
    )
    with patch.object(backend, "_make_request", return_value=mock_resp2) as mock_req:
        chunks2 = list(session.send_message_stream([tool_res]))
        assert len(chunks2) == 1
        assert chunks2[0].text == "File content processed."

        # Verify messages sent to API in second turn
        call_payload = mock_req.call_args[1]["data"]
        messages = call_payload["messages"]
        # Messages should contain: system, user, assistant (with tool_calls), tool response
        assert len(messages) == 4
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert messages[2]["role"] == "assistant"
        assert messages[2]["tool_calls"][0]["function"]["name"] == "read_file"
        assert messages[3]["role"] == "tool"
        assert messages[3]["tool_call_id"] == "call_1"
        assert messages[3]["content"] == "file content here"


def test_get_backend_factory_resolves_proxy_backend():
    config = {
        "active_backend": "Custom 1",
        "backends": {
            "Custom 1": {
                "api": "openai",
                "base_url": "https://api.proxy.com/v1",
                "default_model": "gpt-4o",
            }
        },
    }

    backend = get_backend("Custom 1", config=config)
    assert isinstance(backend, ProxyBackend)
    assert backend.base_url == "https://api.proxy.com/v1"
