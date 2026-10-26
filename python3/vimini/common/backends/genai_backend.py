import logging
from typing import Any, Dict, Iterator, List, Optional
from google.genai import types
from vimini.common.backends.base import (
    BaseChatSession,
    BaseClient,
    StreamChunk,
    ToolCall,
    ToolResponse,
)
from vimini.common.genai import (
    get_client,
    load_api_key,
    create_generation_config,
)

logger = logging.getLogger("vimini_agent")


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
                        tool_calls.append(ToolCall(name=fc.name, args=args_dict))
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
        config: Optional[Any] = None,
    ) -> Iterator[StreamChunk]:
        if config is None:
            config = create_generation_config(system_instruction=system_instruction)
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
            config = create_generation_config(
                tools=tools,
                verbose=verbose,
                system_instruction=system_instruction,
                disable_function_calling=tools is None,
            )
        return GenAIChatSession(
            self.client, model=model, config=config, history=history
        )
