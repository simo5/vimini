from typing import Any, Dict, Optional

from vimini.common.backends.base import (
    BaseChatSession,
    BaseClient,
    FunctionDeclaration,
    Schema,
    StreamChunk,
    Tool,
    ToolCall,
    ToolResponse,
    Type,
)
from vimini.common.backends.genai_backend import GenAIBackend, GenAIChatSession
from vimini.common.backends.proxy_backend import (
    ProxyBackend,
    ProxyChatSession,
    convert_schema_to_openai,
    convert_tools_to_openai,
)
from vimini.common.config import (
    load_global_config,
    get_active_backend,
    load_backend_api_key,
    validate_global_config,
)


def get_backend(
    backend_name: Optional[str] = None,
    config: Optional[Dict[str, Any]] = None,
    config_path: Optional[str] = None,
    api_key: Optional[str] = None,
    api_key_file: Optional[str] = None,
) -> BaseClient:
    """
    Factory function returning a BaseClient instance for the requested or active backend.
    Confines all backend selection logic within generic backend methods.
    """
    if isinstance(config, dict) and "backends" in config:
        validate_global_config(config)
        global_cfg = config
        if backend_name is None:
            backend_name = (
                config.get("active_backend") or config.get("backend") or "Gemini"
            )
    else:
        global_cfg = load_global_config(config_path=config_path)
        if backend_name is None and isinstance(config, dict):
            backend_name = config.get("backend")
        if backend_name is None:
            backend_name = get_active_backend(global_cfg)

    backends = global_cfg.get("backends", {})
    if backend_name not in backends:
        available = list(backends.keys())
        raise ValueError(
            f"Backend '{backend_name}' not found in configuration. Available: {available}"
        )

    backend_cfg = dict(backends[backend_name])
    api = backend_cfg.get("api", "genai")

    if api_key is None:
        api_key = load_backend_api_key(backend_cfg)

    if not api_key:
        key_file = backend_cfg.get("api_key_file")
        raise RuntimeError(
            f"API key for backend '{backend_name}' could not be loaded from "
            f"api_key_file '{key_file}'"
        )

    if api == "genai":
        return GenAIBackend(
            api_key=api_key,
            api_key_file=backend_cfg.get("api_key_file"),
            config=backend_cfg,
        )
    elif api == "openai":
        return ProxyBackend(
            config=backend_cfg,
            api_key=api_key,
            base_url=backend_cfg.get("base_url"),
            headers=backend_cfg.get("headers"),
        )

    raise ValueError(f"Unsupported API protocol '{api}' for backend '{backend_name}'.")


def get_client(
    config: Optional[Dict[str, Any]] = None,
    backend_name: Optional[str] = None,
    config_path: Optional[str] = None,
    api_key: Optional[str] = None,
    api_key_file: Optional[str] = None,
) -> BaseClient:
    """
    Generic client accessor for agent and frontend code.
    Confines all backend selection into generic backend methods.
    """
    return get_backend(
        backend_name=backend_name,
        config=config,
        config_path=config_path,
        api_key=api_key,
        api_key_file=api_key_file,
    )


__all__ = [
    "BaseChatSession",
    "BaseClient",
    "FunctionDeclaration",
    "Schema",
    "StreamChunk",
    "Tool",
    "ToolCall",
    "ToolResponse",
    "Type",
    "GenAIBackend",
    "GenAIChatSession",
    "ProxyBackend",
    "ProxyChatSession",
    "convert_schema_to_openai",
    "convert_tools_to_openai",
    "get_backend",
    "get_client",
]
