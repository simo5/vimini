import logging
from typing import Any, Dict, Iterator, List, Optional
from google.genai import types
from vimini.common.backends.base import (
    BaseChatSession,
    BaseClient,
    FunctionDeclaration,
    Schema,
    StreamChunk,
    Tool,
    ToolCall,
    ToolResponse,
)
from vimini.common.genai import (
    get_client,
    load_api_key,
    create_generation_config,
)

logger = logging.getLogger("vimini_agent")


def _to_genai_schema(schema: Any) -> Any:
    if schema is None:
        return types.Schema(type=types.Type.OBJECT)
    if isinstance(schema, types.Schema):
        return schema
    if isinstance(schema, Schema):
        type_str = str(schema.type).upper().split(".")[-1]
        genai_type = getattr(types.Type, type_str, types.Type.OBJECT)
        props = None
        if schema.properties:
            props = {k: _to_genai_schema(v) for k, v in schema.properties.items()}
        items = None
        if schema.items:
            items = _to_genai_schema(schema.items)
        return types.Schema(
            type=genai_type,
            description=schema.description,
            properties=props,
            required=schema.required,
            enum=schema.enum,
            items=items,
        )
    if isinstance(schema, dict):
        type_val = schema.get("type", "object")
        type_str = str(type_val).upper().split(".")[-1]
        genai_type = getattr(types.Type, type_str, types.Type.OBJECT)
        props = None
        if "properties" in schema and isinstance(schema["properties"], dict):
            props = {k: _to_genai_schema(v) for k, v in schema["properties"].items()}
        items = None
        if "items" in schema:
            items = _to_genai_schema(schema["items"])
        return types.Schema(
            type=genai_type,
            description=schema.get("description"),
            properties=props,
            required=schema.get("required"),
            enum=schema.get("enum"),
            items=items,
        )
    return types.Schema(type=types.Type.OBJECT)


def _to_genai_tools(tools: Any) -> Optional[List[Any]]:
    if not tools:
        return None
    genai_tools = []
    tool_list = tools if isinstance(tools, (list, tuple)) else [tools]
    for t in tool_list:
        if isinstance(t, types.Tool):
            genai_tools.append(t)
        elif isinstance(t, Tool):
            fds = []
            for fd in t.function_declarations:
                if isinstance(fd, types.FunctionDeclaration):
                    fds.append(fd)
                else:
                    fds.append(
                        types.FunctionDeclaration(
                            name=fd.name,
                            description=fd.description or "",
                            parameters=_to_genai_schema(fd.parameters),
                        )
                    )
            genai_tools.append(types.Tool(function_declarations=fds))
        elif isinstance(t, FunctionDeclaration):
            genai_tools.append(
                types.Tool(
                    function_declarations=[
                        types.FunctionDeclaration(
                            name=t.name,
                            description=t.description or "",
                            parameters=_to_genai_schema(t.parameters),
                        )
                    ]
                )
            )
        elif isinstance(t, types.FunctionDeclaration):
            genai_tools.append(types.Tool(function_declarations=[t]))
        elif isinstance(t, dict):
            if t.get("type") == "function" and "function" in t:
                fn = t["function"]
                genai_tools.append(
                    types.Tool(
                        function_declarations=[
                            types.FunctionDeclaration(
                                name=fn.get("name", ""),
                                description=fn.get("description", ""),
                                parameters=_to_genai_schema(fn.get("parameters")),
                            )
                        ]
                    )
                )
            elif "name" in t:
                genai_tools.append(
                    types.Tool(
                        function_declarations=[
                            types.FunctionDeclaration(
                                name=t["name"],
                                description=t.get("description", ""),
                                parameters=_to_genai_schema(t.get("parameters")),
                            )
                        ]
                    )
                )
    return genai_tools if genai_tools else None


class GenAIChatSession(BaseChatSession):
    def __init__(
        self,
        genai_client,
        model: str,
        config: Optional[Any] = None,
        history: Optional[Any] = None,
    ):
        self.client = genai_client
        self.model = model
        self.config = config
        kwargs = {"model": model}
        if config is not None:
            kwargs["config"] = config
        if history is not None:
            kwargs["history"] = history
        self.session = self.client.chats.create(**kwargs)

    def has_history(self) -> bool:
        return True

    def get_history(self) -> List[Any]:
        if self.session is not None and hasattr(self.session, "get_history"):
            try:
                return self.session.get_history()
            except Exception as e:
                logger.warning(f"Error getting GenAI chat history: {e}")
        return []

    def send_message_stream(
        self, prompt_or_tool_responses: Any
    ) -> Iterator[StreamChunk]:
        call_arg = prompt_or_tool_responses
        if isinstance(prompt_or_tool_responses, list):
            converted = []
            for item in prompt_or_tool_responses:
                if isinstance(item, ToolResponse):
                    resp_dict = (
                        item.response
                        if isinstance(item.response, dict)
                        else {"result": item.response}
                    )
                    converted.append(
                        types.Part.from_function_response(
                            name=item.name, response=resp_dict
                        )
                    )
                else:
                    converted.append(item)
            call_arg = converted

        response_stream = self.session.send_message_stream(call_arg)
        for chunk in response_stream:
            text = ""
            thought = ""
            tool_calls = []

            if (
                getattr(chunk, "candidates", None)
                and chunk.candidates
                and getattr(chunk.candidates[0], "content", None)
                and getattr(chunk.candidates[0].content, "parts", None)
            ):
                for part in chunk.candidates[0].content.parts:
                    if hasattr(part, "function_call") and part.function_call:
                        fc = part.function_call
                        args_dict = dict(fc.args) if getattr(fc, "args", None) else {}
                        tool_calls.append(
                            ToolCall(
                                name=fc.name,
                                args=args_dict,
                                call_id=getattr(fc, "id", None),
                            )
                        )
                    elif getattr(part, "thought", False):
                        thought += getattr(part, "text", "") or ""
                    elif hasattr(part, "text") and part.text:
                        text += part.text
            elif getattr(chunk, "text", None):
                text += chunk.text

            yield StreamChunk(text=text, thought=thought, tool_calls=tool_calls)


class GenAIBackend(BaseClient):
    def __init__(
        self,
        api_key: Optional[str] = None,
        api_key_file: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ):
        if not api_key:
            api_key = load_api_key(config=config, api_key_file=api_key_file)
        self.client = get_client(api_key=api_key)

    def list_models(self) -> List[Dict[str, Any]]:
        models_iter = self.client.models.list()
        models = []
        for m in models_iter:
            actions = (
                getattr(m, "supported_actions", None)
                or getattr(m, "supported_generation_methods", None)
                or []
            )
            if isinstance(actions, (list, tuple, set)):
                actions = list(actions)
            else:
                actions = []
            models.append(
                {
                    "name": m.name,
                    "display_name": getattr(m, "display_name", m.name),
                    "description": getattr(m, "description", "") or "",
                    "supported_actions": actions,
                }
            )
        return models

    def generate_content(
        self,
        prompt: str,
        model: str,
        system_instruction: Optional[str] = None,
        config: Optional[Any] = None,
    ) -> str:
        if config is None:
            config = create_generation_config(
                disable_function_calling=True,
                system_instruction=system_instruction,
            )
        response = self.client.models.generate_content(
            model=model, contents=prompt, config=config
        )
        return getattr(response, "text", "") or ""

    def generate_content_stream(
        self,
        prompt: Any,
        model: str,
        system_instruction: Optional[str] = None,
        verbose: bool = False,
        response_schema: Optional[Any] = None,
        config: Optional[Any] = None,
    ) -> Iterator[StreamChunk]:
        if config is None:
            schema_arg = (
                _to_genai_schema(response_schema)
                if response_schema is not None
                else None
            )
            config = create_generation_config(
                verbose=verbose,
                system_instruction=system_instruction,
                response_mime_type="application/json"
                if response_schema is not None
                else None,
                response_schema=schema_arg,
                disable_function_calling=True,
            )
        response_stream = self.client.models.generate_content_stream(
            model=model, contents=prompt, config=config
        )
        for chunk in response_stream:
            text = ""
            thought = ""
            if (
                getattr(chunk, "candidates", None)
                and chunk.candidates
                and getattr(chunk.candidates[0], "content", None)
                and getattr(chunk.candidates[0].content, "parts", None)
            ):
                for part in chunk.candidates[0].content.parts:
                    if getattr(part, "thought", False):
                        thought += getattr(part, "text", "") or ""
                    elif hasattr(part, "text") and part.text:
                        text += part.text
            elif getattr(chunk, "text", None):
                text += chunk.text
            yield StreamChunk(text=text, thought=thought)

    def create_chat(
        self,
        model: str,
        system_instruction: Optional[str] = None,
        tools: Optional[Any] = None,
        history: Optional[Any] = None,
        verbose: bool = False,
        config: Optional[Any] = None,
    ) -> BaseChatSession:
        if config is None:
            genai_tools = _to_genai_tools(tools)
            config = create_generation_config(
                tools=genai_tools,
                verbose=verbose,
                system_instruction=system_instruction,
                disable_function_calling=genai_tools is None,
            )
        return GenAIChatSession(
            self.client, model=model, config=config, history=history
        )

    def upload_context_files(
        self,
        logger: Any,
        file_paths_to_include: Optional[List[str]] = None,
        project_root: Optional[str] = None,
        buffers: Optional[List[Any]] = None,
        display_cb: Optional[Any] = None,
    ) -> List[Any]:
        from vimini.common.context import upload_context_files

        return (
            upload_context_files(
                logger,
                self.client,
                file_paths_to_include=file_paths_to_include,
                project_root=project_root,
                buffers=buffers,
                display_cb=display_cb,
            )
            or []
        )
