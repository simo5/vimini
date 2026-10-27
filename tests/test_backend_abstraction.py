import sys
import os
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
    ToolResponse,
)
from vimini.common.backends.genai_backend import GenAIBackend, GenAIChatSession
from vimini.common.backends.proxy_backend import ProxyBackend
from vimini.common.backends import get_backend


def test_stream_chunk_and_tool_classes():
    tc = ToolCall(name="read_file", args={"filepath": "test.py"}, call_id="123")
    assert tc.name == "read_file"
    assert tc.args == {"filepath": "test.py"}
    assert tc.call_id == "123"

    tr = ToolResponse(name="read_file", response="content", call_id="123")
    assert tr.name == "read_file"
    assert tr.response == "content"
    assert tr.call_id == "123"

    chunk = StreamChunk(text="hello", thought="thinking", tool_calls=[tc])
    assert chunk.text == "hello"
    assert chunk.thought == "thinking"
    assert len(chunk.tool_calls) == 1


def test_get_backend_genai(tmp_path):
    config = {
        "active_backend": "Gemini",
        "backends": {
            "Gemini": {
                "api": "genai",
                "api_key_file": "~/.config/gemini.token",
                "default_model": "gemini-3.8-flash",
            }
        },
    }

    with (
        patch("vimini.common.backends.load_backend_api_key", return_value="fake-key"),
        patch("vimini.common.backends.genai_backend.get_client") as mock_get_client,
    ):
        backend = get_backend("Gemini", config=config)
        assert isinstance(backend, GenAIBackend)
        mock_get_client.assert_called_once()


def test_get_backend_defaults_to_active(tmp_path):
    config = {
        "active_backend": "Gemini",
        "backends": {
            "Gemini": {
                "api": "genai",
                "api_key_file": "~/.config/gemini.token",
                "default_model": "gemini-3.8-flash",
            }
        },
    }

    with (
        patch("vimini.common.backends.load_backend_api_key", return_value="fake-key"),
        patch("vimini.common.backends.genai_backend.get_client"),
    ):
        backend = get_backend(config=config)
        assert isinstance(backend, GenAIBackend)


def test_get_backend_unknown():
    config = {
        "active_backend": "Gemini",
        "backends": {
            "Gemini": {"api": "genai", "api_key_file": "~/.config/gemini.token"}
        },
    }
    with pytest.raises(ValueError, match="Backend 'NonExistent' not found"):
        get_backend("NonExistent", config=config)


def test_get_backend_unsupported_api():
    config = {
        "active_backend": "Custom",
        "backends": {
            "Custom": {
                "api": "unknown_api",
                "api_key_file": "~/.config/custom.token",
            }
        },
    }
    with (
        patch("vimini.common.backends.load_backend_api_key", return_value="fake-key"),
        pytest.raises(ValueError, match="Unsupported API protocol 'unknown_api'"),
    ):
        get_backend("Custom", config=config)


def test_get_backend_openai():
    config = {
        "active_backend": "Custom 1",
        "backends": {
            "Custom 1": {"api": "openai", "api_key_file": "~/.config/openai.token"}
        },
    }
    with patch("vimini.common.backends.load_backend_api_key", return_value="fake-key"):
        backend = get_backend("Custom 1", config=config)
    assert isinstance(backend, ProxyBackend)


def test_get_backend_reports_missing_api_key():
    config = {
        "active_backend": "Custom 1",
        "backends": {
            "Custom 1": {"api": "openai", "api_key_file": "~/.config/missing.token"}
        },
    }

    with (
        patch("vimini.common.backends.load_backend_api_key", return_value=None),
        pytest.raises(RuntimeError, match="API key for backend 'Custom 1'"),
    ):
        get_backend("Custom 1", config=config)


def test_genai_backend_list_models():
    mock_client = MagicMock()
    mock_model = MagicMock()
    mock_model.name = "models/gemini-pro"
    mock_model.display_name = "Gemini Pro"
    mock_model.description = "Pro model"
    mock_model.supported_actions = ["generateContent"]

    mock_client.models.list.return_value = [mock_model]

    with patch(
        "vimini.common.backends.genai_backend.get_client", return_value=mock_client
    ):
        backend = GenAIBackend(api_key="fake-key")
        models = backend.list_models()

        assert len(models) == 1
        assert models[0]["name"] == "models/gemini-pro"
        assert models[0]["display_name"] == "Gemini Pro"
        assert models[0]["description"] == "Pro model"
        assert models[0]["supported_actions"] == ["generateContent"]


def test_genai_backend_generate_content():
    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "Generated text"
    mock_client.models.generate_content.return_value = mock_resp

    with patch(
        "vimini.common.backends.genai_backend.get_client", return_value=mock_client
    ):
        backend = GenAIBackend(api_key="fake-key")
        res = backend.generate_content("Hello", model="gemini-3.8-flash")

        assert res == "Generated text"
        mock_client.models.generate_content.assert_called_once()


def test_genai_backend_generate_content_stream():
    mock_client = MagicMock()
    mock_chunk1 = MagicMock()
    mock_chunk1.candidates = []
    mock_chunk1.text = "Hello "

    mock_part_thought = MagicMock()
    mock_part_thought.thought = True
    mock_part_thought.text = "Thinking..."

    mock_part_text = MagicMock()
    mock_part_text.thought = False
    mock_part_text.function_call = None
    mock_part_text.text = "World"

    mock_chunk2 = MagicMock()
    mock_candidate = MagicMock()
    mock_candidate.content.parts = [mock_part_thought, mock_part_text]
    mock_chunk2.candidates = [mock_candidate]
    mock_chunk2.text = None

    mock_client.models.generate_content_stream.return_value = [
        mock_chunk1,
        mock_chunk2,
    ]

    with patch(
        "vimini.common.backends.genai_backend.get_client", return_value=mock_client
    ):
        backend = GenAIBackend(api_key="fake-key")
        chunks = list(
            backend.generate_content_stream("Prompt", model="gemini-3.8-flash")
        )

        assert len(chunks) == 2
        assert chunks[0].text == "Hello "
        assert chunks[1].thought == "Thinking..."
        assert chunks[1].text == "World"


def test_genai_chat_session():
    mock_client = MagicMock()
    mock_chat_obj = MagicMock()
    mock_client.chats.create.return_value = mock_chat_obj

    mock_chat_obj.get_history.return_value = [{"role": "user", "text": "hi"}]

    with patch(
        "vimini.common.backends.genai_backend.get_client", return_value=mock_client
    ):
        backend = GenAIBackend(api_key="fake-key")
        session = backend.create_chat(model="gemini-3.8-flash")

        assert isinstance(session, GenAIChatSession)
        assert session.has_history() is True
        assert session.get_history() == [{"role": "user", "text": "hi"}]


def test_genai_chat_session_send_message_stream():
    mock_client = MagicMock()
    mock_chat_obj = MagicMock()
    mock_client.chats.create.return_value = mock_chat_obj

    # Create mock response chunks
    mock_chunk = MagicMock()
    mock_fc = MagicMock()
    mock_fc.name = "read_file"
    mock_fc.args = {"filepath": "foo.py"}

    mock_part_fc = MagicMock()
    mock_part_fc.thought = False
    mock_part_fc.function_call = mock_fc

    mock_cand = MagicMock()
    mock_cand.content.parts = [mock_part_fc]
    mock_chunk.candidates = [mock_cand]

    mock_chat_obj.send_message_stream.return_value = [mock_chunk]

    session = GenAIChatSession(mock_client, model="gemini-3.8-flash")
    chunks = list(session.send_message_stream("List files"))

    assert len(chunks) == 1
    assert len(chunks[0].tool_calls) == 1
    assert chunks[0].tool_calls[0].name == "read_file"
    assert chunks[0].tool_calls[0].args == {"filepath": "foo.py"}


def test_genai_chat_session_send_tool_response():
    mock_client = MagicMock()
    mock_chat_obj = MagicMock()
    mock_client.chats.create.return_value = mock_chat_obj

    mock_chunk = MagicMock()
    mock_chunk.candidates = []
    mock_chunk.text = "File contents received."
    mock_chat_obj.send_message_stream.return_value = [mock_chunk]

    session = GenAIChatSession(mock_client, model="gemini-3.8-flash")
    tool_resp = ToolResponse(name="read_file", response="file data")

    with patch(
        "google.genai.types.Part.from_function_response"
    ) as mock_from_fn_response:
        mock_from_fn_response.return_value = MagicMock()
        chunks = list(session.send_message_stream([tool_resp]))

        assert len(chunks) == 1
        assert chunks[0].text == "File contents received."
        mock_from_fn_response.assert_called_once_with(
            name="read_file", response={"result": "file data"}
        )
