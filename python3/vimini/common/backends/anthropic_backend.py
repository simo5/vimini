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


def convert_schema_to_anthropic(schema: Any) -> Dict[str, Any]:
    """
    Converts a Google GenAI types.Schema, Vimini Schema, or dict into
    an Anthropic-compatible JSON schema.
    """
    if schema is None:
        return {"type": "object", "properties": {}}

    if isinstance(schema, dict):
        res = dict(schema)
        if "type" in res:
            res["type"] = str(res["type"]).lower().split(".")[-1]
        if "properties" in res and isinstance(res["properties"], dict):
            res["properties"] = {
                k: convert_schema_to_anthropic(v) for k, v in res["properties"].items()
            }
        if "items" in res and res["items"] is not None:
            res["items"] = convert_schema_to_anthropic(res["items"])
        res.setdefault("type", "object")
        if res.get("type") == "object":
            res.setdefault("properties", {})
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
            k: convert_schema_to_anthropic(v) for k, v in properties.items()
        }

    required = getattr(schema, "required", None)
    if required and isinstance(required, list):
        res["required"] = required

    enum = getattr(schema, "enum", None)
    if enum and isinstance(enum, list):
        res["enum"] = enum

    items = getattr(schema, "items", None)
    if items is not None:
        res["items"] = convert_schema_to_anthropic(items)

    if res.get("type") == "object":
        res.setdefault("properties", {})

    return res


def convert_tools_to_anthropic(tools: Any) -> List[Dict[str, Any]]:
    """
    Converts a list of GenAI Tool, FunctionDeclaration, or dict objects
    to Anthropic tools format.
    """
    if not tools:
        return []

    anthropic_tools: List[Dict[str, Any]] = []
    tool_items = tools if isinstance(tools, (list, tuple)) else [tools]

    for item in tool_items:
        if isinstance(item, dict):
            if "name" in item and "input_schema" in item:
                anthropic_tools.append(item)
            elif item.get("type") == "function" and "function" in item:
                fn = item["function"]
                anthropic_tools.append(
                    {
                        "name": fn.get("name", ""),
                        "description": fn.get("description", ""),
                        "input_schema": convert_schema_to_anthropic(
                            fn.get("parameters")
                        ),
                    }
                )
            elif "name" in item:
                anthropic_tools.append(
                    {
                        "name": item["name"],
                        "description": item.get("description", ""),
                        "input_schema": convert_schema_to_anthropic(
                            item.get("parameters") or item.get("input_schema")
                        ),
                    }
                )
            continue

        # Handle google.genai.types.Tool or vimini.common.backends.base.Tool
        if hasattr(item, "function_declarations") and item.function_declarations:
            for fd in item.function_declarations:
                anthropic_tools.append(
                    {
                        "name": fd.name,
                        "description": getattr(fd, "description", "") or "",
                        "input_schema": convert_schema_to_anthropic(
                            getattr(fd, "parameters", None)
                        ),
                    }
                )
            continue

        # Handle FunctionDeclaration
        if hasattr(item, "name"):
            anthropic_tools.append(
                {
                    "name": item.name,
                    "description": getattr(item, "description", "") or "",
                    "input_schema": convert_schema_to_anthropic(
                        getattr(item, "parameters", None)
                    ),
                }
            )

    return anthropic_tools


class AnthropicChatSession(BaseChatSession):
    def __init__(
        self,
        backend: "AnthropicBackend",
        model: str,
        system_instruction: Optional[str] = None,
        tools: Optional[List[Dict[str, Any]]] = None,
    ):
        self.backend = backend
        self.model = model
        self.system_instruction = system_instruction
        self.tools = tools or []
        self.messages: List[Dict[str, Any]] = []

    def has_history(self) -> bool:
        return False

    def get_history(self) -> List[Any]:
        return []

    def _append_user_content(self, content: Any) -> None:
        """
        Appends user content while strictly preserving Anthropic's rule that
        messages must alternate between 'user' and 'assistant'.
        """
        if self.messages and self.messages[-1].get("role") == "user":
            prev = self.messages[-1]["content"]
            if isinstance(prev, str) and isinstance(content, str):
                self.messages[-1]["content"] = f"{prev}\n\n{content}"
            elif isinstance(prev, list) and isinstance(content, list):
                self.messages[-1]["content"] = prev + content
            elif isinstance(prev, list) and isinstance(content, str):
                self.messages[-1]["content"] = prev + [
                    {"type": "text", "text": content}
                ]
            elif isinstance(prev, str) and isinstance(content, list):
                self.messages[-1]["content"] = [
                    {"type": "text", "text": prev}
                ] + content
        else:
            self.messages.append({"role": "user", "content": content})

    def send_message_stream(
        self, prompt_or_tool_responses: Any
    ) -> Iterator[StreamChunk]:
        if isinstance(prompt_or_tool_responses, str):
            self._append_user_content(prompt_or_tool_responses)
        elif isinstance(prompt_or_tool_responses, ToolResponse):
            content = (
                prompt_or_tool_responses.response
                if isinstance(prompt_or_tool_responses.response, str)
                else json.dumps(prompt_or_tool_responses.response)
            )
            self._append_user_content(
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": prompt_or_tool_responses.call_id or "",
                        "content": content,
                    }
                ]
            )
        elif isinstance(prompt_or_tool_responses, list):
            tool_results = []
            text_items = []
            for item in prompt_or_tool_responses:
                if isinstance(item, ToolResponse):
                    content = (
                        item.response
                        if isinstance(item.response, str)
                        else json.dumps(item.response)
                    )
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": item.call_id or "",
                            "content": content,
                        }
                    )
                elif isinstance(item, dict):
                    if item.get("type") == "tool_result":
                        tool_results.append(item)
                    elif "tool_call_id" in item:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": item["tool_call_id"],
                                "content": str(item.get("content", "")),
                            }
                        )
                    elif "role" in item:
                        self.messages.append(item)
                    else:
                        tool_results.append(item)
                else:
                    text_items.append(str(item))

            if tool_results:
                self._append_user_content(tool_results)
            elif text_items:
                self._append_user_content("\n\n".join(text_items))
        elif isinstance(prompt_or_tool_responses, dict):
            if "role" in prompt_or_tool_responses:
                self.messages.append(prompt_or_tool_responses)
            else:
                self._append_user_content(json.dumps(prompt_or_tool_responses))

        payload: Dict[str, Any] = {
            "model": self.model,
            "max_tokens": self.backend.max_tokens,
            "messages": list(self.messages),
            "stream": True,
        }
        if self.system_instruction:
            payload["system"] = self.system_instruction
        if self.tools:
            payload["tools"] = self.tools

        resp = self.backend._make_request("/messages", data=payload, stream=True)

        accumulated_text = ""
        accumulated_thought = ""
        content_blocks: Dict[int, Dict[str, Any]] = {}

        for event_data in self.backend._read_sse_stream(resp):
            event_type = event_data.get("type")

            if event_type == "content_block_start":
                idx = event_data.get("index", 0)
                content_blocks[idx] = dict(event_data.get("content_block", {}))
                if content_blocks[idx].get("type") == "tool_use":
                    content_blocks[idx]["partial_json"] = ""

            elif event_type == "content_block_delta":
                idx = event_data.get("index", 0)
                delta = event_data.get("delta", {})
                delta_type = delta.get("type")
                text_chunk = ""
                thought_chunk = ""

                if delta_type == "text_delta":
                    text_chunk = delta.get("text", "") or ""
                    accumulated_text += text_chunk
                    if idx not in content_blocks:
                        content_blocks[idx] = {"type": "text", "text": ""}
                    content_blocks[idx]["text"] = (
                        content_blocks[idx].get("text", "") + text_chunk
                    )
                elif delta_type == "thinking_delta":
                    thought_chunk = delta.get("thinking", "") or ""
                    accumulated_thought += thought_chunk
                    if idx not in content_blocks:
                        content_blocks[idx] = {"type": "thinking", "thinking": ""}
                    content_blocks[idx]["thinking"] = (
                        content_blocks[idx].get("thinking", "") + thought_chunk
                    )
                elif delta_type == "signature_delta":
                    sig = delta.get("signature", "") or ""
                    if idx in content_blocks:
                        content_blocks[idx]["signature"] = (
                            content_blocks[idx].get("signature", "") + sig
                        )
                elif delta_type == "input_json_delta":
                    partial = delta.get("partial_json", "") or ""
                    if idx not in content_blocks:
                        content_blocks[idx] = {"type": "tool_use", "partial_json": ""}
                    content_blocks[idx]["partial_json"] = (
                        content_blocks[idx].get("partial_json", "") + partial
                    )

                if text_chunk or thought_chunk:
                    yield StreamChunk(text=text_chunk, thought=thought_chunk)

            elif event_type == "content_block_stop":
                pass

        # Assemble completed tool calls
        completed_tool_calls: List[ToolCall] = []
        for idx in sorted(content_blocks.keys()):
            block = content_blocks[idx]
            if block.get("type") == "tool_use":
                call_id = block.get("id") or f"toolu_{idx}"
                name = block.get("name", "")
                partial_json = block.get("partial_json", "")
                if partial_json:
                    try:
                        args = json.loads(partial_json)
                    except Exception:
                        args = {"raw": partial_json}
                else:
                    args = block.get("input") or {}
                if not isinstance(args, dict):
                    args = {"input": args}
                completed_tool_calls.append(
                    ToolCall(name=name, args=args, call_id=call_id)
                )

        if completed_tool_calls:
            yield StreamChunk(tool_calls=completed_tool_calls)

        # Record assistant response in conversation history
        asst_content = []
        for idx in sorted(content_blocks.keys()):
            block = content_blocks[idx]
            b_type = block.get("type")
            if b_type == "text":
                txt = block.get("text", "")
                if txt:
                    asst_content.append({"type": "text", "text": txt})
            elif b_type == "thinking":
                thk = block.get("thinking", "")
                thk_block = {"type": "thinking", "thinking": thk}
                if "signature" in block:
                    thk_block["signature"] = block["signature"]
                asst_content.append(thk_block)
            elif b_type == "tool_use":
                call_id = block.get("id") or f"toolu_{idx}"
                name = block.get("name", "")
                partial_json = block.get("partial_json", "")
                if partial_json:
                    try:
                        args = json.loads(partial_json)
                    except Exception:
                        args = {"raw": partial_json}
                else:
                    args = block.get("input") or {}
                asst_content.append(
                    {
                        "type": "tool_use",
                        "id": call_id,
                        "name": name,
                        "input": args,
                    }
                )

        if asst_content:
            self.messages.append({"role": "assistant", "content": asst_content})
        elif accumulated_text:
            self.messages.append({"role": "assistant", "content": accumulated_text})


class AnthropicBackend(BaseClient):
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

        url = base_url or self.config.get("base_url") or "https://api.anthropic.com/v1"
        url = url.rstrip("/")
        if not url.endswith("/v1") and "anthropic.com" in url:
            url = f"{url}/v1"
        self.base_url = url

        self.headers = dict(headers or self.config.get("headers", {}))
        self.anthropic_version = self.config.get("anthropic_version") or "2023-06-01"
        self.max_tokens = int(self.config.get("max_tokens") or 4096)

    def _make_request(
        self, endpoint: str, data: Optional[Dict[str, Any]] = None, stream: bool = False
    ):
        url = f"{self.base_url}{endpoint}"
        req_headers = {
            "Content-Type": "application/json",
            "anthropic-version": self.anthropic_version,
            "User-Agent": "Vimini/AnthropicAPI",
        }
        if self.api_key:
            req_headers["x-api-key"] = self.api_key
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
            logger.error(f"Anthropic HTTP error {e.code} for {url}: {err_body}")
            raise RuntimeError(f"Anthropic HTTP error {e.code}: {err_body}") from e
        except Exception as e:
            logger.error(f"Anthropic request error for {url}: {e}")
            raise RuntimeError(f"Anthropic connection error: {e}") from e

    def _read_sse_stream(self, resp) -> Iterator[Dict[str, Any]]:
        buffer = ""

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
                    if event is not None:
                        if event.get("type") == "error":
                            err = event.get("error", {})
                            msg = err.get("message") or str(err)
                            raise RuntimeError(f"Anthropic streaming error: {msg}")
                        yield event
                        if event.get("type") == "message_stop":
                            return
                buffer = normalized

            if buffer.strip():
                event = parse_event(buffer)
                if event is not None:
                    if event.get("type") == "error":
                        err = event.get("error", {})
                        msg = err.get("message") or str(err)
                        raise RuntimeError(f"Anthropic streaming error: {msg}")
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
                disp_name = m.get("display_name") or m.get("name") or m_id
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
        if isinstance(prompt, list):
            prompt_str = "\n\n".join(str(p) for p in prompt if p)
        elif isinstance(prompt, str):
            prompt_str = prompt
        else:
            prompt_str = str(prompt)

        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt_str}],
            "stream": False,
        }
        if system_instruction:
            payload["system"] = system_instruction

        data = self._make_request("/messages", data=payload, stream=False)
        content = data.get("content", [])
        if isinstance(content, list):
            text_parts = [
                b.get("text", "")
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return "".join(text_parts)
        elif isinstance(content, str):
            return content
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
        if isinstance(prompt, list):
            prompt_str = "\n\n".join(str(p) for p in prompt if p)
        elif isinstance(prompt, str):
            prompt_str = prompt
        else:
            prompt_str = str(prompt)

        payload: Dict[str, Any] = {
            "model": model,
            "max_tokens": self.max_tokens,
            "messages": [{"role": "user", "content": prompt_str}],
            "stream": True,
        }
        if system_instruction:
            payload["system"] = system_instruction

        resp = self._make_request("/messages", data=payload, stream=True)
        for event_data in self._read_sse_stream(resp):
            event_type = event_data.get("type")
            if event_type == "content_block_delta":
                delta = event_data.get("delta", {})
                delta_type = delta.get("type")
                text_chunk = ""
                thought_chunk = ""
                if delta_type == "text_delta":
                    text_chunk = delta.get("text", "") or ""
                elif delta_type == "thinking_delta":
                    thought_chunk = delta.get("thinking", "") or ""
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
        anthropic_tools = convert_tools_to_anthropic(tools)
        return AnthropicChatSession(
            backend=self,
            model=model,
            system_instruction=system_instruction,
            tools=anthropic_tools,
        )
