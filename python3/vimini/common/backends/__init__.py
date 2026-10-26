from typing import Any, Dict, Optional

from vimini.common.backends.base import (
    BaseChatSession,
    BaseClient,
    StreamChunk,
    ToolCall,
    ToolResponse,
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
) -> BaseClient:
    """
    Factory function returning a BaseClient instance for the requested or active backend.
    """
    if config is None:
        config = load_global_config(config_path=config_path)

    backends = config.get("backends", {})

    if backend_name is None:
        backend_name = get_active_backend(config)

    if backend_name not in backends:
        available = list(backends.keys())
        raise ValueError(
            f"Backend '{backend_name}' not found in configuration. Available: {available}"
        )

    backend_cfg = backends[backend_name]
    api = backend_cfg.get("api", "genai")

    if api == "genai":
        api_key = load_backend_api_key(backend_cfg)
        return GenAIBackend(
            api_key=api_key,
            api_key_file=backend_cfg.get("api_key_file"),
            config=backend_cfg,
        )
    elif api == "openai":
        api_key = load_backend_api_key(backend_cfg)
        return ProxyBackend(
            config=backend_cfg,
            api_key=api_key,
            base_url=backend_cfg.get("base_url"),
            headers=backend_cfg.get("headers"),
        )

    raise ValueError(f"Unsupported API protocol '{api}' for backend '{backend_name}'.")


__all__ = [
    "BaseChatSession",
    "BaseClient",
    "StreamChunk",
    "ToolCall",
    "ToolResponse",
    "GenAIBackend",
    "GenAIChatSession",
    "ProxyBackend",
    "ProxyChatSession",
    "convert_schema_to_openai",
    "convert_tools_to_openai",
    "get_backend",
]
