import os
import json
import logging

logger = logging.getLogger("vimini_agent")

DEFAULT_CONFIG_DIR = os.path.expanduser("~/.var/vimini")
DEFAULT_CONFIG_PATH = os.path.join(DEFAULT_CONFIG_DIR, "config.json")
DEFAULT_BACKEND = "Gemini"
DEFAULT_MODEL = "gemini-3.8-flash"
DEFAULT_GEMINI_KEY_FILE = "~/.config/gemini.token"
DEFAULT_ANTHROPIC_KEY_FILE = "~/.config/anthropic.token"
DEFAULT_ANTHROPIC_MODEL = "claude-3-7-sonnet-20250219"
SUPPORTED_API_TYPES = {"genai", "openai", "anthropic"}
VERSIONED_API_TYPES = ("openai", "anthropic")
DEFAULT_API_VERSION = "v1"

DEFAULT_UI_CONFIG = {
    "thinking_color": "DarkGray",
    "prompt_color": "DarkBlue",
    "waiting_color": "Green",
    "service_color": "Green",
    "error_color": "Red",
    "prompt_hint_color": "Green",
}


def _to_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ("1", "true", "yes", "on"):
            return True
        if normalized in ("0", "false", "no", "off"):
            return False
    return default


def backend_requires_api_key(backend_config):
    """
    Returns whether a backend requires an API key.

    Gemini and Anthropic always require an API key by default. OpenAI-compatible
    endpoints default to requiring one if api_key_file is defined and non-empty,
    and not requiring one if omitted (e.g. for local keyless endpoints). Explicit
    requires_api_key takes precedence if specified.
    """
    if not isinstance(backend_config, dict):
        return True
    api = str(backend_config.get("api", "genai")).strip()
    default = api in ("genai", "anthropic") or bool(backend_config.get("api_key_file"))
    return _to_bool(backend_config.get("requires_api_key"), default=default)


def get_global_config_path():
    """
    Returns the path to the global configuration file ~/.var/vimini/config.json.
    """
    return DEFAULT_CONFIG_PATH


def strip_api_version(base_url, api_version=DEFAULT_API_VERSION):
    """
    Removes a trailing API version segment (e.g. '/v1') from a base URL.

    Base URLs are stored and handled without the API version so that the
    version is applied by the backend itself, allowing future support for
    multiple API versions (and version discovery) without reconfiguration.
    """
    if not isinstance(base_url, str):
        return base_url

    url = base_url.strip().rstrip("/")
    if not url:
        return ""

    version = str(api_version or "").strip().strip("/")
    if version:
        suffix = f"/{version}"
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
    return url


def normalize_backend_base_url(backend_config, api_version=DEFAULT_API_VERSION):
    """
    Normalizes a single backend 'base_url' in place by stripping the API
    version suffix. Returns True if the configuration was modified.
    """
    if not isinstance(backend_config, dict):
        return False

    api = str(backend_config.get("api", "")).strip()
    if api not in VERSIONED_API_TYPES:
        return False

    base_url = backend_config.get("base_url")
    if not isinstance(base_url, str) or not base_url.strip():
        return False

    normalized = strip_api_version(base_url, api_version=api_version)
    if normalized != base_url:
        backend_config["base_url"] = normalized
        return True
    return False


def normalize_global_config_base_urls(config):
    """
    Normalizes every OpenAI/Anthropic backend 'base_url' in a global
    configuration by stripping any legacy API version suffix. Returns True if
    any backend configuration was modified.
    """
    if not isinstance(config, dict):
        return False

    backends = config.get("backends")
    if not isinstance(backends, dict):
        return False

    changed = False
    for backend_config in backends.values():
        if normalize_backend_base_url(backend_config):
            changed = True
    return changed


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
        "ui": dict(DEFAULT_UI_CONFIG),
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
        api = api.strip()
        if api not in SUPPORTED_API_TYPES:
            supported = ", ".join(sorted(SUPPORTED_API_TYPES))
            raise ValueError(
                f"Backend '{name}' api '{api}' is not supported. Supported APIs: {supported}"
            )
        requires_key = backend_requires_api_key(backend_info)
        key_file = backend_info.get("api_key_file")
        if key_file is not None and not isinstance(key_file, str):
            raise ValueError(
                f"Backend '{name}' 'api_key_file' must be a string path if specified"
            )
        if requires_key and (not isinstance(key_file, str) or not key_file.strip()):
            raise ValueError(
                f"Backend '{name}' must specify a non-empty 'api_key_file'"
            )
        default_model = backend_info.get("default_model")
        if api in ("openai", "anthropic") and (
            not isinstance(default_model, str) or not default_model.strip()
        ):
            raise ValueError(
                f"Backend '{name}' must specify a non-empty 'default_model'"
            )

    ui = config.get("ui")
    if ui is not None:
        if not isinstance(ui, dict):
            raise ValueError("Global configuration 'ui' must be a dictionary")
        for key, val in ui.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError(
                    "Global configuration 'ui' keys must be non-empty strings"
                )
            if val is not None and not isinstance(val, str):
                raise ValueError(f"Global configuration 'ui.{key}' must be a string")
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

    config = validate_global_config(data)

    # Migrate legacy base_url values that still embed the API version so the
    # stored configuration remains version agnostic.
    if normalize_global_config_base_urls(config):
        try:
            save_global_config(config, config_path=target_path)
        except Exception as e:
            logger.warning(
                f"Failed to persist normalized base_url values to '{target_path}': {e}"
            )

    return config


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
