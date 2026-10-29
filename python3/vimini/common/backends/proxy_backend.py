import json
import logging
import urllib.request
import urllib.error
from typing import Any, Dict, Iterator, List, Optional

from vimini.common.backends.base import (
    BaseChatSession,
    BaseClient,
    StreamChunk,
    ToolCall,
    ToolResponse,
)
from vimini.common.config import (
    DEFAULT_API_VERSION,
    load_backend_api_key,
    strip_api_version,
)

logger = logging.getLogger("vimini_agent")

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com"


def convert_schema_to_openai(schema: Any) -> Dict[str, Any]:
    """
    Converts a Google GenAI types.Schema or dict into OpenAI-compatible JSON schema.
    """
    if schema is None:
        return {"type": "object", "properties": {}}

    if isinstance(schema, dict):
        res = dict(schema)
        if "type" in res:
            res["type"] = str(res["type"]).lower().split(".")[-1]
        if "properties" in res and isinstance(res["properties"], dict):
            res["properties"] = {
                k: convert_schema_to_openai(v) for k, v in res["properties"].items()
            }
        return res

    type_val = getattr(schema, "type", "object")
    type_str = str(type_val).lower().split(".")[-1]
    if type_str == "type":
        type_str = "object"

    res: Dict[str, Any] = {"type": type_str}

    desc = getattr(schema, "description", None)
    if desc:
        res["description"] = desc

    properties = getattr(schema, "properties", None)
    if properties and isinstance(properties, dict):
        res["properties"] = {
            k: convert_schema_to_openai(v) for k, v in properties.items()
        }

    required = getattr(schema, "required", None)
    if required and isinstance(required, list):
        res["required"] = required

    enum = getattr(schema, "enum", None)
    if enum and isinstance(enum, list):
        res["enum"] = enum

    return res


def convert_tools_to_openai(tools: Any) -> List[Dict[str, Any]]:
    """
    Converts a list of GenAI Tool or FunctionDeclaration objects to OpenAI tools format.
    """
    if not tools:
        return []

    openai_tools: List[Dict[str, Any]] = []

    tool_items = tools if isinstance(tools, (list, tuple)) else [tools]

    for item in tool_items:
        if isinstance(item, dict):
            if item.get("type") == "function" and "function" in item:
                openai_tools.append(item)
            elif "name" in item:
                openai_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": item["name"],
                            "description": item.get("description", ""),
                            "parameters": convert_schema_to_openai(
                                item.get("parameters")
                            ),
                        },
                    }
                )
            continue

        # Handle google.genai.types.Tool
        if hasattr(item, "function_declarations") and item.function_declarations:
            for fd in item.function_declarations:
                openai_tools.append(
                    {
                        "type": "function",
                        "function": {
                            "name": fd.name,
                            "description": getattr(fd, "description", "") or "",
                            "parameters": convert_schema_to_openai(
                                getattr(fd, "parameters", None)
                            ),
                        },
                    }
                )
            continue

        # Handle google.genai.types.FunctionDeclaration
        if hasattr(item, "name"):
            openai_tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": item.name,
                        "description": getattr(item, "description", "") or "",
                        "parameters": convert_schema_to_openai(
                            getattr(item, "parameters", None)
                        ),
                    },
                }
            )

    return openai_tools


class ProxyChatSession(BaseChatSession):
    def __init__(
        self,
        backend: "ProxyBackend",
        model: str,
        system_instruction: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ):
        self.backend = backend
        self.model = model
        self.tools = tools or []
        self.messages: List[Dict[str, Any]] = []
        if system_instruction:
            self.messages.append({"role": "system", "content": system_instruction})

    def has_history(self) -> bool:
        return False

    def get_history(self) -> List[Any]:
        return []

    @staticmethod
    def _merge_streamed_function_name(current: str, incoming: str) -> str:
        """
        Merge streamed function name deltas without duplicating repeated names.

        Most providers stream function names as either one complete value or as
        chunks. Some OpenAI-compatible providers repeat the complete function
        name in subsequent deltas. Preserve chunked names while avoiding
        corruption such as "read_fileread_file".
        """
        if not incoming:
            return current or ""
        if not current or incoming == current:
            return incoming
        if incoming.startswith(current):
            return incoming
        return current + incoming

    @staticmethod
    def _merge_streamed_tool_call(
        target: Dict[str, Any], source: Dict[str, Any]
    ) -> None:
        """
        Merge a streamed OpenAI-compatible tool_call delta.

        Some providers expose OpenAI-compatible APIs while still requiring
        opaque provider metadata to be replayed verbatim on the assistant
        tool-call message in the next request. Google Gemini is one such
        provider: when thinking/tool calls are used, it may attach a
        thought_signature to function-call parts, and later rejects the
        conversation if that signature is dropped.

        Keep this strictly inside the ProxyAPI backend: the public Vimini
        abstraction still exposes only ToolCall(name, args, call_id), while the
        backend preserves unknown protocol fields in its own message history.
        """
        if source.get("id"):
            target["id"] = source.get("id")
        if source.get("type"):
            target["type"] = source.get("type")

        source_function = source.get("function") or {}
        target_function = target.setdefault("function", {})
        if source_function.get("name"):
            target_function["name"] = ProxyChatSession._merge_streamed_function_name(
                target_function.get("name", ""), source_function.get("name", "")
            )
        if source_function.get("arguments"):
            target_function["arguments"] = target_function.get(
                "arguments", ""
            ) + source_function.get("arguments", "")

        # Preserve provider-specific function metadata, for example Gemini's
        # opaque thought_signature, without exposing it through ToolCall.
        for key, value in source_function.items():
            if key in ("name", "arguments"):
                continue
            if value is not None:
                target_function[key] = value

        # Preserve provider-specific top-level metadata as well.
        for key, value in source.items():
            if key in ("index", "id", "type", "function"):
                continue
            if value is not None:
                target[key] = value

    def send_message_stream(
        self, prompt_or_tool_responses: Any
    ) -> Iterator[StreamChunk]:
        if isinstance(prompt_or_tool_responses, str):
            self.messages.append({"role": "user", "content": prompt_or_tool_responses})
        elif isinstance(prompt_or_tool_responses, list):
            for i, item in enumerate(prompt_or_tool_responses):
                if isinstance(item, ToolResponse):
                    content = (
                        item.response
                        if isinstance(item.response, str)
                        else json.dumps(item.response)
                    )
                    call_id = item.call_id or f"call_{i}"
                    self.messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": call_id,
                            "name": item.name,
                            "content": content,
                        }
                    )
                elif isinstance(item, dict):
                    self.messages.append(item)

        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": list(self.messages),
            "stream": True,
        }
        if self.tools:
            payload["tools"] = self.tools

        resp = self.backend._make_request(
            "/chat/completions", data=payload, stream=True
        )

        accumulated_text = ""
        accumulated_thought = ""
        tool_call_chunks: Dict[int, Dict[str, Any]] = {}

        for event_data in self.backend._read_sse_stream(resp):
            choices = event_data.get("choices", [])
            if not choices:
                continue

            delta = choices[0].get("delta", {})
            text_chunk = delta.get("content") or ""
            thought_chunk = (
                delta.get("reasoning_content") or delta.get("reasoning") or ""
            )

            if text_chunk:
                accumulated_text += text_chunk
            if thought_chunk:
                accumulated_thought += thought_chunk

            raw_tool_calls = delta.get("tool_calls")
            if raw_tool_calls:
                for tc in raw_tool_calls:
                    idx = tc.get("index", 0)
                    if idx not in tool_call_chunks:
                        tool_call_chunks[idx] = {
                            "id": tc.get("id"),
                            "type": tc.get("type") or "function",
                            "function": {"name": "", "arguments": ""},
                        }
                    self._merge_streamed_tool_call(tool_call_chunks[idx], tc)

            if text_chunk or thought_chunk:
                yield StreamChunk(text=text_chunk, thought=thought_chunk)

        # Assemble completed tool calls
        completed_tool_calls: List[ToolCall] = []
        if tool_call_chunks:
            for idx in sorted(tool_call_chunks.keys()):
                tc_info = tool_call_chunks[idx]
                call_id = tc_info.get("id") or f"call_{idx}"
                fn_info = tc_info.get("function", {})
                args_str = fn_info.get("arguments", "")
                try:
                    args_dict = json.loads(args_str) if args_str else {}
                except Exception:
                    args_dict = {"raw": args_str}

                completed_tool_calls.append(
                    ToolCall(
                        name=fn_info.get("name", ""),
                        args=args_dict,
                        call_id=call_id,
                    )
                )

            yield StreamChunk(tool_calls=completed_tool_calls)

        # Record assistant response in local conversation history
        asst_msg: Dict[str, Any] = {"role": "assistant"}
        if accumulated_text:
            asst_msg["content"] = accumulated_text
        if completed_tool_calls:
            asst_msg["tool_calls"] = []
            for idx, tc in zip(sorted(tool_call_chunks.keys()), completed_tool_calls):
                raw_tool_call = dict(tool_call_chunks.get(idx, {}))
                raw_function = dict(raw_tool_call.get("function", {}))
                raw_function["name"] = tc.name
                raw_function["arguments"] = json.dumps(tc.args)

                raw_tool_call["id"] = tc.call_id or f"call_{idx}"
                raw_tool_call["type"] = raw_tool_call.get("type") or "function"
                raw_tool_call["function"] = raw_function
                asst_msg["tool_calls"].append(raw_tool_call)
        if accumulated_text or completed_tool_calls:
            self.messages.append(asst_msg)


class ProxyBackend(BaseClient):
    def __init__(
        self,
        config: Optional[Dict[str, Any]] = None,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ):
        self.config = config or {}
        if api_key is None and config:
            api_key = load_backend_api_key(config)
        self.api_key = api_key

        self.api_version = (
            str(self.config.get("api_version") or DEFAULT_API_VERSION)
            .strip()
            .strip("/")
        )

        url = base_url or self.config.get("base_url") or DEFAULT_OPENAI_BASE_URL
        # The API version is owned by the backend, not the configuration, so
        # strip it if a legacy versioned base_url is supplied.
        self.base_url = strip_api_version(url, api_version=self.api_version)
        self.headers = dict(headers or self.config.get("headers", {}))

    @property
    def api_base_url(self) -> str:
        """
        Returns the versioned base URL used for API requests.
        """
        if not self.api_version:
            return self.base_url
        return f"{self.base_url}/{self.api_version}"

    def _make_request(
        self, endpoint: str, data: Optional[Dict[str, Any]] = None, stream: bool = False
    ):
        url = f"{self.api_base_url}{endpoint}"
        req_headers = {
            "Content-Type": "application/json",
            "User-Agent": "Vimini/ProxyAPI",
        }
        if self.api_key:
            req_headers["Authorization"] = f"Bearer {self.api_key}"
        req_headers.update(self.headers)

        body_bytes = json.dumps(data).encode("utf-8") if data is not None else None
        req = urllib.request.Request(
            url,
            data=body_bytes,
            headers=req_headers,
            method="POST" if data is not None else "GET",
        )

        try:
            resp = urllib.request.urlopen(req, timeout=120 if stream else 60)
            if stream:
                return resp
            resp_bytes = resp.read()
            return json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            logger.error(f"ProxyAPI HTTP error {e.code} for {url}: {err_body}")
            raise RuntimeError(f"ProxyAPI HTTP error {e.code}: {err_body}") from e
        except Exception as e:
            logger.error(f"ProxyAPI request error for {url}: {e}")
            raise RuntimeError(f"ProxyAPI connection error: {e}") from e

    def _read_sse_stream(self, resp) -> Iterator[Dict[str, Any]]:
        buffer = ""
        done_sentinel = object()

        def parse_event(event_text: str):
            data_lines = []
            for raw_event_line in event_text.splitlines():
                event_line = raw_event_line.rstrip("\r")
                if not event_line or event_line.startswith(":"):
                    continue
                if event_line.startswith("data:"):
                    data_lines.append(event_line[5:].lstrip())

            if not data_lines:
                return None

            data_str = "\n".join(data_lines)
            if data_str == "[DONE]":
                return done_sentinel
            try:
                return json.loads(data_str)
            except json.JSONDecodeError:
                return None

        try:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace")
                buffer += line
                normalized = buffer.replace("\r\n", "\n").replace("\r", "\n")
                while "\n\n" in normalized:
                    event_text, normalized = normalized.split("\n\n", 1)
                    event = parse_event(event_text)
                    if event is done_sentinel:
                        return
                    if event is not None:
                        yield event
                buffer = normalized

            if buffer.strip():
                event = parse_event(buffer)
                if event is done_sentinel:
                    return
                if event is not None:
                    yield event
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def list_models(self) -> List[Dict[str, Any]]:
        data = self._make_request("/models", stream=False)
        raw_models = data if isinstance(data, list) else data.get("data", [])
        models: List[Dict[str, Any]] = []
        for m in raw_models:
            if isinstance(m, str):
                m_id = m
                disp_name = m
                desc = ""
            elif isinstance(m, dict):
                m_id = m.get("id") or m.get("name")
                disp_name = m.get("name") or m_id
                desc = m.get("description", "")
            else:
                continue

            if not m_id:
                continue

            models.append(
                {
                    "name": m_id,
                    "display_name": disp_name,
                    "description": desc,
                    "supported_actions": ["generateContent"],
                }
            )
        return models

    def generate_content(
        self,
        prompt: Any,
        model: str,
        system_instruction: Optional[str] = None,
        verbose: bool = False,
        response_schema: Optional[Any] = None,
        config: Optional[Any] = None,
    ) -> str:
        messages: List[Dict[str, Any]] = []
        if isinstance(prompt, list):
            prompt_str = "\n\n".join(str(p) for p in prompt if p)
        elif isinstance(prompt, str):
            prompt_str = prompt
        else:
            prompt_str = str(prompt)
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt_str})

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        data = self._make_request("/chat/completions", data=payload, stream=False)
        choices = data.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            return msg.get("content", "") or ""
        return ""

    def generate_content_stream(
        self,
        prompt: Any,
        model: str,
        system_instruction: Optional[str] = None,
        verbose: bool = False,
        response_schema: Optional[Any] = None,
        config: Optional[Any] = None,
    ) -> Iterator[StreamChunk]:
        messages: List[Dict[str, Any]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})

        if isinstance(prompt, list):
            prompt_str = "\n\n".join(str(p) for p in prompt if p)
        elif isinstance(prompt, str):
            prompt_str = prompt
        else:
            prompt_str = str(prompt)
        messages.append({"role": "user", "content": prompt_str})

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
        }
        if response_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        resp = self._make_request("/chat/completions", data=payload, stream=True)
        for event_data in self._read_sse_stream(resp):
            choices = event_data.get("choices", [])
            if not choices:
                continue
            delta = choices[0].get("delta", {})
            text_chunk = delta.get("content") or ""
            thought_chunk = (
                delta.get("reasoning_content") or delta.get("reasoning") or ""
            )
            if text_chunk or thought_chunk:
                yield StreamChunk(text=text_chunk, thought=thought_chunk)

    def create_chat(
        self,
        model: str,
        system_instruction: Optional[str] = None,
        tools: Optional[Any] = None,
        history: Optional[Any] = None,
        verbose: bool = False,
        config: Optional[Any] = None,
    ) -> BaseChatSession:
        openai_tools = convert_tools_to_openai(tools)
        return ProxyChatSession(
            backend=self,
            model=model,
            system_instruction=system_instruction,
            tools=openai_tools,
        )
