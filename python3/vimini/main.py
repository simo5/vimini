import vim
import os, subprocess, shlex, textwrap, json, tempfile, time
from vimini import util
from vimini.util import process_queue, get_model_name
from vimini.autocomplete import autocomplete, cancel_autocomplete
from vimini.code import code, show_diff, apply_code
from vimini.commit import (
    commit,
    finalize_commit,
    _finalize_commit,
    toggle_file,
)
from vimini.review import review
from vimini.ripgrep import command as ripgrep_command
from vimini.ripgrep import apply as ripgrep_apply
from vimini.chat import chat
from vimini.context import (
    context_files_command,
    toggle_context_file,
    show_context_lists,
    confirm_context_files,
    files_command,
    restore_context_files,
)
from vimini.config import config_command, global_config_command
from vimini.handler import (
    BaseChannelHandler,
    register_handler,
    unregister_handler,
    get_handler,
)


def initialize(api_key_file, model=None, logfile=None, backend=None):
    """
    Initializes the plugin with the user's API key file path and optional logfile path.
    The model is always read from the active backend in the global config.
    This function is called from the plugin's Vimscript entry point.
    """
    from vimini.common.config import (
        DEFAULT_MODEL,
        load_global_config,
        get_active_backend,
        backend_requires_api_key,
    )

    config_error = None
    active_b_cfg = {}
    try:
        cfg = load_global_config(api_key_file=api_key_file)
        if not backend:
            backend = get_active_backend(cfg)
        util._BACKEND = backend
        active_b_cfg = cfg.get("backends", {}).get(backend, {})
        if active_b_cfg.get("api_key_file"):
            api_key_file = os.path.expanduser(active_b_cfg.get("api_key_file"))
        active_model = active_b_cfg.get("default_model") or DEFAULT_MODEL
    except Exception as e:
        util.log_info(f"Failed to load global config during initialize: {e}")
        config_error = e
        util._BACKEND = backend or "Gemini"
        active_model = DEFAULT_MODEL

    util._API_KEY_FILE = api_key_file
    util._MODEL = active_model
    util._MODEL_NAME = None
    util._GENAI_CLIENT = None  # Reset client if key/model changes.
    util.set_logging(logfile)
    try:
        vim.command(f"let g:vimini_backend = {json.dumps(util._BACKEND)}")
        vim.command(f"let g:vimini_model = {json.dumps(util._MODEL)}")
    except Exception:
        pass
    restore_context_files()
    if config_error is not None:
        util.display_message(
            f"Global configuration is invalid: {config_error}", error=True
        )
    if backend_requires_api_key(active_b_cfg) and not util.get_api_key():
        backend_name = util._BACKEND or "selected backend"
        key_file = active_b_cfg.get("api_key_file") or api_key_file
        util.display_message(
            f"API key for backend '{backend_name}' not found in {key_file}.", error=True
        )


class SetupChannelHandler(BaseChannelHandler):
    """Handles responses for agent setup."""

    def handle_response(self, result):
        self.finished = True
        util.log_info("Agent server setup completed.")

    def handle_error(self, error):
        self.finished = True
        err_msg = (
            error.get("message", "Unknown error")
            if isinstance(error, dict)
            else str(error)
        )
        util.log_info(f"Agent server setup error: {err_msg}")


class ListModelsChannelHandler(BaseChannelHandler):
    """Handles responses for list_models requests."""

    def handle_response(self, result):
        self.finished = True
        models = result.get("models", [])
        from vimini.models import show_models_list

        show_models_list(models)

    def handle_error(self, error):
        self.finished = True
        err_msg = (
            error.get("message", "Unknown error")
            if isinstance(error, dict)
            else str(error)
        )
        util.display_message(f"Error: {err_msg}", error=True)


def send_setup():
    """
    Sends a setup request to the agent server with internal configuration.
    """
    handler = SetupChannelHandler("setup")
    register_handler("setup", handler)
    backend_name = getattr(util, "_BACKEND", None)
    if not backend_name:
        try:
            backend_name = vim.eval("get(g:, 'vimini_backend', '')")
        except Exception:
            backend_name = None
    if not backend_name:
        try:
            from vimini.common.config import load_global_config, get_active_backend

            cfg = load_global_config()
            backend_name = get_active_backend(cfg)
        except Exception:
            backend_name = "Gemini"
    req = {
        "jsonrpc": "2.0",
        "id": "setup",
        "method": "setup",
        "params": {
            "backend": backend_name,
            "model": util._MODEL,
        },
    }
    return util.send_channel_request(req, silent=False)


def apply_backend_config(cfg, backend_name, persist=True):
    """
    Applies a backend from a loaded global config to the current Vim/runtime state.
    Optionally persists active_backend in the config file.
    """
    from vimini.common.config import set_active_backend

    backends = cfg.get("backends", {}) if isinstance(cfg, dict) else {}
    if backend_name not in backends:
        available = list(backends.keys())
        raise ValueError(f"Backend '{backend_name}' not found. Available: {available}")

    if persist:
        set_active_backend(backend_name)

    target_cfg = backends[backend_name]
    util._BACKEND = backend_name
    try:
        vim.command(f"let g:vimini_backend = {json.dumps(backend_name)}")
    except Exception:
        pass

    if target_cfg.get("default_model"):
        util._MODEL = target_cfg.get("default_model")
        util._MODEL_NAME = None
        try:
            vim.command(f"let g:vimini_model = {json.dumps(util._MODEL)}")
        except Exception:
            pass
    if target_cfg.get("api_key_file"):
        util._API_KEY_FILE = os.path.expanduser(target_cfg.get("api_key_file"))
    send_setup()


def backend_command(backend_name=None):
    """
    Handles :ViminiBackend [backend_name].
    Without arguments, echoes active backend and lists other available backends.
    With argument, validates backend exists, updates active backend, updates model to default,
    and sends setup to agent server.
    """
    from vimini.common.config import (
        load_global_config,
        get_active_backend,
    )

    try:
        cfg = load_global_config()
    except Exception as e:
        util.display_message(f"Error loading configuration: {e}", error=True)
        return

    active = get_active_backend(cfg)
    backends = cfg.get("backends", {})

    backend_name_str = str(backend_name).strip() if backend_name else ""
    if not backend_name_str:
        others = [b for b in backends.keys() if b != active]
        if others:
            util.display_message(
                f"{active} (available: {', '.join(others)})", history=True
            )
        else:
            util.display_message(
                f"{active} (no other backends configured)", history=True
            )
        return

    if backend_name_str not in backends:
        available = list(backends.keys())
        util.display_message(
            f"Backend '{backend_name_str}' not found. Available: {available}",
            error=True,
        )
        return

    try:
        apply_backend_config(cfg, backend_name_str, persist=True)
    except Exception as e:
        util.display_message(f"Error setting active backend: {e}", error=True)
        return
    util.display_message(
        f"Switched backend to {backend_name_str} (model: {util._MODEL})",
        history=True,
    )


def get_available_backends(arg_lead=""):
    """
    Returns a list of backend names matching arg_lead for command completion.
    """
    from vimini.common.config import load_global_config

    try:
        cfg = load_global_config()
        backends = list(cfg.get("backends", {}).keys())
        if arg_lead:
            return [b for b in backends if b.lower().startswith(arg_lead.lower())]
        return backends
    except Exception:
        return []


def start_agent():
    """
    Starts the agent server process, sends a setup request, and returns its Unix socket path.
    """
    try:
        from vimini.agent.server import start_agent_server

        socket_path = start_agent_server()
        if socket_path:
            start_time = time.time()
            while time.time() - start_time < 5:
                if os.path.exists(socket_path):
                    break
                time.sleep(0.1)
        return socket_path
    except Exception as e:
        util.log_info(f"Failed to start agent server: {e}")
        return None


def stop_agent():
    """
    Stops the agent server process if running.
    """
    try:
        from vimini.agent.server import stop_agent_server

        stop_agent_server()
    except Exception as e:
        util.log_info(f"Failed to stop agent server: {e}")


def _send_channel_request(req_dict, silent=False):
    if not vim.eval(
        "exists('g:vimini_channel') && type(g:vimini_channel) == v:t_channel && ch_status(g:vimini_channel) ==# 'open'"
    ):
        if not silent:
            util.display_message("Error: Agent server channel is not open.", error=True)
        return False
    try:
        util.log_info(f"Sending channel request: {req_dict}")
        safe_json = json.dumps(req_dict)
        vim.command(
            f"call ch_sendexpr(g:vimini_channel, json_decode({json.dumps(safe_json)}))"
        )
        return True
    except Exception as e:
        if not silent:
            util.display_message(f"Error sending channel request: {e}", error=True)
        return False


def handle_channel_message(msg):
    """
    Handles JSON channel messages received from the agent server via Vim channel.
    Dispatches directly to registered BaseChannelHandler instances by request ID.
    """
    util.log_info(f"Received channel message: {msg}")
    if not isinstance(msg, dict):
        return

    req_id = msg.get("id")
    error = msg.get("error")
    result = msg.get("result")

    handler = get_handler(req_id) if req_id is not None else None
    if handler is not None:
        if error is not None:
            handler.handle_error(error)
        elif isinstance(result, dict):
            handler.handle_response(result)
        if handler.is_finished():
            unregister_handler(req_id)
        return

    # Fallback for unexpected or untracked error messages
    if error is not None:
        err_msg = (
            error.get("message", "Unknown error")
            if isinstance(error, dict)
            else str(error)
        )
        util.display_message(f"Error: {err_msg}", error=True)


# This new function is needed because vimini.vim calls main.logging()
def logging(logfile=None):
    util.set_logging(logfile)


def reload_vimini():
    """
    Reloads the vimini python modules to pick up changes from disk.
    Also re-initializes the plugin to preserve the API key file and settings.
    """
    import sys
    import os
    import vim

    # Save initialization parameters before deleting modules
    try:
        from vimini import util as old_util

        api_key_file = old_util._API_KEY_FILE
        job_counter = old_util._JOB_COUNTER
        log_file = None
        if old_util._LOGGER and old_util._LOGGER.handlers:
            import logging

            for handler in old_util._LOGGER.handlers:
                if isinstance(handler, logging.FileHandler):
                    log_file = handler.baseFilename
                    break
    except Exception:
        api_key_file = os.path.expanduser("~/.config/gemini.token")
        job_counter = 0
        log_file = vim.eval("get(g:, 'vimini_log_file', '')")
        if not log_file or vim.eval("get(g:, 'vimini_logging', 'off')") != "on":
            log_file = None

    # Delete all vimini modules from sys.modules
    modules_to_delete = [m for m in list(sys.modules.keys()) if m.startswith("vimini")]
    for m in modules_to_delete:
        del sys.modules[m]

    # Re-import main and re-initialize
    from vimini import main

    main.initialize(api_key_file=api_key_file, logfile=log_file)

    # Use the freshly imported util to display the message and restore _JOB_COUNTER
    from vimini import util as new_util

    new_util._JOB_COUNTER = job_counter
    new_util.display_message("Vimini Python modules reloaded.", history=True)


def list_models():
    """
    Lists the available Gemini models.
    """
    util.log_info("list_models()")
    handler = ListModelsChannelHandler("list_models")
    register_handler("list_models", handler)
    return {
        "jsonrpc": "2.0",
        "id": "list_models",
        "method": "list_models",
        "params": {},
    }


def help(command_name=None):
    """
    Opens a read-only buffer with descriptions of available commands.
    If command_name is provided, scrolls to it and highlights it.
    """
    util.log_info(f"help(command_name='{command_name}')")

    help_content = [
        "VIMINI HELP",
        "===========",
        "",
        ":ViminiBackend [backend_name]",
        "    Switches or displays the active model backend (e.g. Gemini, ProxyAPI).",
        "    Without arguments, echoes active backend and other available backends.",
        "",
        ":ViminiListModels",
        "    Lists available models. Press D on a model to save it as the backend default.",
        "",
        ":ViminiChat",
        "    Opens the chat buffer for interactive mode with Gemini.",
        "",
        ":ViminiThinking [on|off]",
        "    Toggles or sets the display of the AI's real-time thought process.",
        "",
        ":ViminiToggleLogging [on|off]",
        "    Toggles or sets the logging feature to file.",
        "",
        ":ViminiCode {prompt}",
        "    Generates code based on open buffers and context files.",
        "    Output goes to 'Vimini Diff'. Use :ViminiApply to apply changes.",
        "",
        ":ViminiApply",
        "    Applies changes from 'Vimini Diff' to actual files.",
        "",
        ":ViminiContextFiles",
        "    Opens a file manager to manage files sent as context (g:context_files).",
        "",
        ":ViminiConfig",
        "    Opens a guided editor to configure project settings (build/test/format commands, worktree directory).",
        "",
        ":ViminiGlobalConfig",
        "    Opens a guided editor to configure global settings and model provider backends.",
        "",
        ":ViminiReview [-c <git_objects>] [--pr <number>] [--mr <number>] [--remote <remote>] [--security] [--save[=<path>]] [{prompt}]",
        "    Reviews code in current buffer, git objects, GitHub PR, or GitLab MR.",
        "    -c <ref>: Review changes in git ref (can be a range for batch review).",
        "    --pr <number>: Fetch and review GitHub Pull Request.",
        "    --mr <number>: Fetch and review GitLab Merge Request.",
        "    --remote <remote>: Remote repository to fetch from (defaults to g:vimini_default_remote, upstream, branch remote, or origin).",
        "    --security: Focus on security vulnerabilities.",
        "    --save: Save reviews to text files (useful with batch review).",
        "    --save=path: Save reviews to a specific directory (defaults to g:vimini_review_path).",
        "",
        ":ViminiDiff",
        "    Shows 'git diff' output in a buffer.",
        "",
        ":ViminiCommit [-n] [-r] [-a] [instruction]",
        "    Generates a commit message and commits changes.",
        "    Files to be committed are listed in the diff stat comments.",
        "    Press <Space> in Normal mode to toggle files off (marked with #-) or on.",
        "    -n: No co-author trailer.",
        "    -r: Regenerate/Amend HEAD.",
        "    -a: Amend changes into HEAD and regenerate commit message.",
        "    [instruction]: Optional hint for the commit message generation.",
        "",
        ":ViminiFiles",
        "    Manages remote files uploaded to Gemini.",
        "",
        ":ViminiToggleAutocomplete [on|off]",
        "    Toggles real-time ghost-text autocomplete.",
        "",
        ":ViminiRipGrep {regex} {prompt}",
        "    Search with ripgrep and modify results with AI.",
        "    Example: :ViminiRipGrep 'TODO' 'Remove all TODOs'",
        "",
        ":ViminiRipGrepApply",
        "    Apply changes from ViminiRipGrep buffer.",
        "",
        ":ViminiStatus",
        "    Shows a read-only window with all currently active jobs.",
        "",
        ":ViminiReload",
        "    Reloads the Vimini Python code from disk for faster development iterations.",
        "",
        ":ViminiHelp [command]",
    ]

    # Find or create buffer
    buf_name = "Vimini Help"
    win_nr = vim.eval(f"bufwinnr('^{buf_name}$')")

    if int(win_nr) > 0:
        vim.command(f"{win_nr}wincmd w")
    else:
        util.new_split()
        vim.command(f"file {buf_name}")
        vim.command("setlocal buftype=nofile filetype=markdown noswapfile")

    # Update content
    vim.command("setlocal modifiable")
    vim.current.buffer[:] = help_content
    vim.command("setlocal nomodifiable")

    # Highlight handling
    vim.command("try | call clearmatches() | catch | endtry")

    if command_name:
        target = command_name.lstrip(":")
        # Find the line starting with :Target
        found_line = -1
        search_prefix = f":{target}"

        for i, line in enumerate(help_content):
            if line.strip().startswith(search_prefix):
                found_line = i + 1
                break

        if found_line != -1:
            vim.command(f"normal! {found_line}Gzz")
            # Highlight the command name
            pattern = search_prefix.replace("'", "''")
            vim.command(f"call matchadd('Search', '{pattern}')")
        else:
            util.display_message(f"Command :{target} not found in help.", history=True)


def status_command():
    util.show_status()
