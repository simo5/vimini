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
from vimini.common.config import load_backend_api_key

logger = logging.getLogger("vimini_agent")


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
                            "name": tc.get("function", {}).get("name", ""),
                            "arguments": tc.get("function", {}).get("arguments", ""),
                        }
                    else:
                        if tc.get("id"):
                            tool_call_chunks[idx]["id"] = tc.get("id")
                        if tc.get("function", {}).get("name"):
                            tool_call_chunks[idx]["name"] += tc.get("function", {}).get(
                                "name"
                            )
                        if tc.get("function", {}).get("arguments"):
                            tool_call_chunks[idx]["arguments"] += tc.get(
                                "function", {}
                            ).get("arguments")

            if text_chunk or thought_chunk:
                yield StreamChunk(text=text_chunk, thought=thought_chunk)

        # Assemble completed tool calls
        completed_tool_calls: List[ToolCall] = []
        if tool_call_chunks:
            for idx in sorted(tool_call_chunks.keys()):
                tc_info = tool_call_chunks[idx]
                call_id = tc_info.get("id") or f"call_{idx}"
                args_str = tc_info.get("arguments", "")
                try:
                    args_dict = json.loads(args_str) if args_str else {}
                except Exception:
                    args_dict = {"raw": args_str}

                completed_tool_calls.append(
                    ToolCall(name=tc_info["name"], args=args_dict, call_id=call_id)
                )

            yield StreamChunk(tool_calls=completed_tool_calls)

        # Record assistant response in local conversation history
        asst_msg: Dict[str, Any] = {"role": "assistant"}
        if accumulated_text:
            asst_msg["content"] = accumulated_text
        if completed_tool_calls:
            asst_msg["tool_calls"] = [
                {
                    "id": tc.call_id or f"call_{i}",
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": json.dumps(tc.args),
                    },
                }
                for i, tc in enumerate(completed_tool_calls)
            ]
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

        url = base_url or self.config.get("base_url") or "https://api.openai.com/v1"
        self.base_url = url.rstrip("/")
        self.headers = dict(headers or self.config.get("headers", {}))

    def _make_request(
        self, endpoint: str, data: Optional[Dict[str, Any]] = None, stream: bool = False
    ):
        url = f"{self.base_url}{endpoint}"
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
        try:
            for raw_line in resp:
                line = raw_line.decode("utf-8", errors="replace")
                buffer += line
                while "\n" in buffer:
                    line_str, buffer = buffer.split("\n", 1)
                    line_str = line_str.strip()
                    if not line_str or line_str.startswith(":"):
                        continue
                    if line_str.startswith("data:"):
                        data_str = line_str[5:].strip()
                        if data_str == "[DONE]":
                            return
                        try:
                            yield json.loads(data_str)
                        except json.JSONDecodeError:
                            continue
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
        prompt: str,
        model: str,
        system_instruction: Optional[str] = None,
        verbose: bool = False,
        response_schema: Optional[Any] = None,
        config: Optional[Any] = None,
    ) -> str:
        messages: List[Dict[str, Any]] = []
        if system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

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

        prompt_str = prompt if isinstance(prompt, str) else str(prompt)
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
