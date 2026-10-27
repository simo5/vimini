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
    global_cfg = load_global_config(config_path=config_path)
    backends = global_cfg.get("backends", {})

    if backend_name is None and isinstance(config, dict):
        backend_name = config.get("backend")

    if backend_name is None:
        backend_name = get_active_backend(global_cfg)

    if backend_name not in backends:
        # Check if config itself has backends dictionary
        if isinstance(config, dict) and "backends" in config:
            backends = config.get("backends", {})
        if backend_name not in backends:
            available = list(backends.keys())
            raise ValueError(
                f"Backend '{backend_name}' not found in configuration. Available: {available}"
            )

    backend_cfg = dict(backends[backend_name])
    if isinstance(config, dict):
        # Allow runtime overrides from caller config if present
        for k in ("api_key", "api_key_file", "base_url", "headers"):
            if k in config and config[k] is not None:
                backend_cfg[k] = config[k]

    api = backend_cfg.get("api", "genai")

    if api_key is None:
        api_key = load_backend_api_key(backend_cfg)

    target_key_file = api_key_file or backend_cfg.get("api_key_file")

    if api == "genai":
        return GenAIBackend(
            api_key=api_key,
            api_key_file=target_key_file,
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
