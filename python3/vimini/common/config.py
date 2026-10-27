import os
import json
import logging

logger = logging.getLogger("vimini_agent")

DEFAULT_CONFIG_DIR = os.path.expanduser("~/.var/vimini")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.json")
DEFAULT_BACKEND = "Gemini"
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_GEMINI_KEY_FILE = "~/.config/gemini.token"


def get_global_config_path():
    """
    Returns the path to the global configuration file ~/.var/vimini/config.json.
    """
    return DEFAULT_CONFIG_PATH


def create_default_global_config(default_model=None, api_key_file=None):
    """
    Generates a default v1 configuration dictionary.
    """
    return {
        "active_backend": DEFAULT_BACKEND,
        "backends": {
            DEFAULT_BACKEND: {
                "api": "genai",
                "api_key_file": api_key_file or DEFAULT_GEMINI_KEY_FILE,
                "default_model": default_model or DEFAULT_MODEL,
            }
        },
    }


def validate_global_config(config):
    """
    Validates the structure of global configuration data.
    Raises ValueError if invalid.
    """
    if not isinstance(config, dict):
        raise ValueError(
            f"Global configuration must be a dictionary, got {type(config).__name__}"
        )

    active_backend = config.get("active_backend")
    if not isinstance(active_backend, str) or not active_backend.strip():
        raise ValueError(
            "Global configuration must specify a non-empty 'active_backend'"
        )

    backends = config.get("backends")
    if not isinstance(backends, dict) or not backends:
        raise ValueError(
            "Global configuration must contain a non-empty 'backends' dictionary"
        )

    if active_backend not in backends:
        raise ValueError(
            f"Active backend '{active_backend}' is not defined in 'backends'"
        )

    for name, backend_info in backends.items():
        if not isinstance(backend_info, dict):
            raise ValueError(f"Backend configuration for '{name}' must be a dictionary")
        api = backend_info.get("api")
        if not isinstance(api, str) or not api.strip():
            raise ValueError(f"Backend '{name}' must specify a non-empty 'api' type")
        key_file = backend_info.get("api_key_file")
        if key_file is not None and not isinstance(key_file, str):
            raise ValueError(
                f"Backend '{name}' 'api_key_file' must be a string path if specified"
            )
        if not isinstance(key_file, str) or not key_file.strip():
            raise ValueError(
                f"Backend '{name}' must specify a non-empty 'api_key_file'"
            )

    return config


def load_global_config(config_path=None, default_model=None, api_key_file=None):
    """
    Loads configuration from ~/.var/vimini/config.json.
    If the file does not exist, generates a default v1 config, saves it, and returns it.
    """
    target_path = (
        os.path.realpath(os.path.expanduser(config_path))
        if config_path
        else DEFAULT_CONFIG_PATH
    )

    if not os.path.exists(target_path):
        default_config = create_default_global_config(
            default_model=default_model, api_key_file=api_key_file
        )
        save_global_config(default_config, config_path=target_path)
        return default_config

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        raise ValueError(
            f"Failed to parse global configuration file '{target_path}': {e}"
        )

    return validate_global_config(data)


def save_global_config(config, config_path=None):
    """
    Saves the global configuration dictionary to ~/.var/vimini/config.json.
    """
    target_path = (
        os.path.realpath(os.path.expanduser(config_path))
        if config_path
        else DEFAULT_CONFIG_PATH
    )

    validate_global_config(config)

    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    temp_target = f"{target_path}.tmp.{os.getpid()}"
    try:
        with open(temp_target, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2)
            f.write("\n")
        os.replace(temp_target, target_path)
    finally:
        if os.path.exists(temp_target):
            try:
                os.remove(temp_target)
            except OSError:
                pass

    return True


def get_active_backend(config):
    """
    Returns the name of the active backend.
    """
    if isinstance(config, dict):
        return config.get("active_backend", DEFAULT_BACKEND)
    return DEFAULT_BACKEND


def get_active_backend_config(config):
    """
    Returns a tuple (backend_name, backend_config_dict) for the active backend.
    """
    if not isinstance(config, dict):
        return None, None
    name = get_active_backend(config)
    backends = config.get("backends", {})
    return name, backends.get(name)


def set_active_backend(backend_name, config=None, config_path=None):
    """
    Switches the active backend in the configuration and saves the changes.
    """
    target_path = (
        os.path.realpath(os.path.expanduser(config_path))
        if config_path
        else DEFAULT_CONFIG_PATH
    )

    if config is None:
        config = load_global_config(config_path=target_path)

    backends = config.get("backends", {})
    if backend_name not in backends:
        available = list(backends.keys())
        raise ValueError(
            f"Backend '{backend_name}' not found. Available backends: {available}"
        )

    config["active_backend"] = backend_name
    save_global_config(config, config_path=target_path)
    return config


def load_backend_api_key(backend_config):
    """
    Loads API key for a backend from its configured api_key_file.
    """
    if not isinstance(backend_config, dict):
        return None

    api_key_file = backend_config.get("api_key_file")
    if not api_key_file:
        return None

    expanded_path = os.path.expanduser(api_key_file)
    if os.path.exists(expanded_path):
        try:
            with open(expanded_path, "r", encoding="utf-8") as f:
                content = f.read().strip()
                return content if content else None
        except Exception as e:
            logger.error(f"Error reading API key file '{expanded_path}': {e}")
            raise RuntimeError(f"Error reading API key file '{expanded_path}': {e}")

    return None
