import os
import logging
from google import genai
from google.genai import types

logger = logging.getLogger("vimini_agent")


def load_api_key(config=None, api_key_file=None):
    """
    Loads API key from config dict or file path.
    """
    if isinstance(config, dict):
        if config.get("api_key"):
            return config.get("api_key")
        if not api_key_file:
            api_key_file = config.get("api_key_file")

    if api_key_file:
        expanded_path = os.path.expanduser(api_key_file)
        if os.path.exists(expanded_path):
            try:
                with open(expanded_path, "r", encoding="utf-8") as f:
                    return f.read().strip()
            except Exception as e:
                logger.error(f"Error reading API key file '{expanded_path}': {e}")
                raise RuntimeError(f"Error reading API key file '{expanded_path}': {e}")
    return None


def get_client(api_key=None, api_key_file=None, config=None):
    """
    Initializes and returns a genai.Client instance.
    """
    if not api_key:
        api_key = load_api_key(config=config, api_key_file=api_key_file)
    if api_key:
        return genai.Client(api_key=api_key)
    return genai.Client()


def create_generation_config(
    temperature=None,
    verbose=False,
    include_thoughts=None,
    response_mime_type=None,
    response_schema=None,
    tools=None,
    system_instruction=None,
    disable_function_calling=None,
    thinking_config=None,
    **kwargs,
):
    """
    Creates and returns a types.GenerateContentConfig tailored for the use case.
    Handles temperature validation and conversion, thoughts/verbose flags,
    automatic function calling configuration, response schemas, and tools.
    """
    config_kwargs = {}
    if disable_function_calling is None:
        disable_function_calling = tools is None

    if disable_function_calling:
        config_kwargs["automatic_function_calling"] = (
            types.AutomaticFunctionCallingConfig(disable=True)
        )

    if tools is not None:
        config_kwargs["tools"] = tools

    if system_instruction is not None:
        config_kwargs["system_instruction"] = system_instruction

    if response_mime_type is not None:
        config_kwargs["response_mime_type"] = response_mime_type

    if response_schema is not None:
        config_kwargs["response_schema"] = response_schema

    if thinking_config is not None:
        config_kwargs["thinking_config"] = thinking_config
    elif verbose or include_thoughts:
        config_kwargs["thinking_config"] = types.ThinkingConfig(include_thoughts=True)

    config_kwargs.update(kwargs)

    config = types.GenerateContentConfig(**config_kwargs)

    if temperature is not None:
        try:
            temp_float = float(temperature)
            if 0.0 <= temp_float <= 2.0:
                config.temperature = temp_float
        except (ValueError, TypeError):
            pass

    return config
