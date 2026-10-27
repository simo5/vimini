from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Iterator, List, Optional


class TypeEnum:
    def __init__(self, name: str):
        self.name = name

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"Type.{self.name}"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, str):
            return self.name.lower() == other.lower()
        if hasattr(other, "name"):
            return self.name.lower() == str(other.name).lower()
        return False

    def __hash__(self) -> int:
        return hash(self.name.lower())


class Type:
    OBJECT = TypeEnum("OBJECT")
    STRING = TypeEnum("STRING")
    INTEGER = TypeEnum("INTEGER")
    BOOLEAN = TypeEnum("BOOLEAN")
    ARRAY = TypeEnum("ARRAY")


@dataclass
class Schema:
    type: str = Type.OBJECT
    description: Optional[str] = None
    properties: Optional[Dict[str, Any]] = None
    required: Optional[List[str]] = None
    enum: Optional[List[str]] = None
    items: Optional[Any] = None


@dataclass
class FunctionDeclaration:
    name: str
    description: Optional[str] = None
    parameters: Optional[Schema] = None


@dataclass
class Tool:
    function_declarations: List[FunctionDeclaration] = field(default_factory=list)


@dataclass
class ToolCall:
    name: str
    args: Dict[str, Any] = field(default_factory=dict)
    call_id: Optional[str] = None


@dataclass
class ToolResponse:
    name: str
    response: Any
    call_id: Optional[str] = None


@dataclass
class StreamChunk:
    text: str = ""
    thought: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)


class BaseChatSession(ABC):
    @abstractmethod
    def send_message_stream(
        self, prompt_or_tool_responses: Any
    ) -> Iterator[StreamChunk]:
        """
        Sends a prompt or tool responses to the model and yields StreamChunk instances.
        """
        pass

    @abstractmethod
    def has_history(self) -> bool:
        """
        Returns whether the chat session supports history export.
        """
        pass

    @abstractmethod
    def get_history(self) -> List[Any]:
        """
        Retrieves serializable chat history if supported.
        """
        pass


class BaseClient(ABC):
    @abstractmethod
    def list_models(self) -> List[Dict[str, Any]]:
        """
        Returns standardized model records:
        [
            {
                "name": str,
                "display_name": str,
                "description": str,
                "supported_actions": List[str],
            },
            ...
        ]
        """
        pass

    @abstractmethod
    def generate_content(
        self,
        prompt: str,
        model: str,
        system_instruction: Optional[str] = None,
        config: Optional[Any] = None,
    ) -> str:
        """
        Non-streaming content generation.
        """
        pass

    @abstractmethod
    def generate_content_stream(
        self,
        prompt: Any,
        model: str,
        system_instruction: Optional[str] = None,
        verbose: bool = False,
        response_schema: Optional[Any] = None,
        config: Optional[Any] = None,
    ) -> Iterator[StreamChunk]:
        """
        Streaming content generation yielding StreamChunk instances.
        """
        pass

    @abstractmethod
    def create_chat(
        self,
        model: str,
        system_instruction: Optional[str] = None,
        tools: Optional[Any] = None,
        history: Optional[Any] = None,
        verbose: bool = False,
        config: Optional[Any] = None,
    ) -> BaseChatSession:
        """
        Creates and returns a BaseChatSession for multi-turn chat.
        """
        pass

    def upload_context_files(
        self,
        logger: Any,
        file_paths_to_include: Optional[List[str]] = None,
        project_root: Optional[str] = None,
        buffers: Optional[List[Any]] = None,
        display_cb: Optional[Any] = None,
    ) -> List[Any]:
        """
        Uploads or processes context files if supported by backend.
        """
        return []
