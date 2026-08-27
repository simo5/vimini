import vim
import os, subprocess, shlex, textwrap, json, tempfile, time
from google.genai import types
from vimini import util
from vimini.util import process_queue, get_model_name
from vimini.autocomplete import autocomplete, cancel_autocomplete
from vimini.code import code, show_diff, apply_code
from vimini.commit import commit, handle_commit_response, finalize_commit, _finalize_commit
from vimini.review import review
from vimini.ripgrep import command as ripgrep_command
from vimini.ripgrep import apply as ripgrep_apply
from vimini.chat import chat
from vimini.context import context_files_command, toggle_context_file, show_context_lists, confirm_context_files, files_command, restore_context_files
from vimini.config import config_command

def initialize(api_key_file, model, logfile=None):
    """
    Initializes the plugin with the user's API key file path, model name, and
    optional logfile path.
    This function is called from the plugin's Vimscript entry point.
    """
    util._API_KEY_FILE = api_key_file
    util._MODEL = model
    util._GENAI_CLIENT = None # Reset client if key/model changes.
    util.set_logging(logfile)
    restore_context_files()
    if not util.get_api_key():
        util.display_message("API key not found. Please store it in ~/.config/gemini.token.", error=True)

def send_setup():
    """
    Sends a setup request to the agent server with internal configuration.
    """
    temperature = None
    try:
        temperature = vim.eval("get(g:, 'vimini_temperature', v:null)")
    except Exception:
        pass
    req = {
        "jsonrpc": "2.0",
        "id": "setup",
        "method": "setup",
        "params": {
            "api_key_file": util._API_KEY_FILE,
            "model": util._MODEL,
            "temperature": temperature
        }
    }
    return util.send_channel_request(req, silent=False)

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
    if not vim.eval("exists('g:vimini_channel') && type(g:vimini_channel) == v:t_channel && ch_status(g:vimini_channel) ==# 'open'"):
        if not silent:
            util.display_message("Error: Agent server channel is not open.", error=True)
        return False
    try:
        util.log_info(f"Sending channel request: {req_dict}")
        safe_json = json.dumps(req_dict)
        vim.command(f"call ch_sendexpr(g:vimini_channel, json_decode({json.dumps(safe_json)}))")
        return True
    except Exception as e:
        if not silent:
            util.display_message(f"Error sending channel request: {e}", error=True)
        return False

def handle_channel_message(msg):
    """
    Handles JSON channel messages received from the agent server via Vim channel.
    """
    util.log_info(f"Received channel message: {msg}")
    if not isinstance(msg, dict):
        return

    req_id = msg.get("id")
    method = msg.get("method")
    error = msg.get("error")
    result = msg.get("result")

    if error is not None:
        err_msg = error.get("message", "Unknown error") if isinstance(error, dict) else str(error)
        err_result = {"status": "error", "error": err_msg}
        if method == "autocomplete":
            from vimini.autocomplete import handle_channel_response
            handle_channel_response(req_id, err_result)
        elif method == "code":
            from vimini.code import handle_channel_response
            handle_channel_response(req_id, err_result)
        elif method == "chat":
            from vimini.chat import handle_channel_response
            handle_channel_response(req_id, err_result)
        elif method == "review":
            from vimini.review import handle_channel_response
            handle_channel_response(req_id, err_result)
        elif method == "commit":
            util.display_message(f"Error: {err_msg}", error=True)
        elif method == "list_models":
            util.display_message(f"Error: {err_msg}", error=True)
        else:
            util.display_message(f"Error: {err_msg}", error=True)
        return

    if isinstance(result, dict):
        if method == "autocomplete":
            from vimini.autocomplete import handle_channel_response
            handle_channel_response(req_id, result)
        elif method == "code":
            from vimini.code import handle_channel_response
            handle_channel_response(req_id, result)
        elif method == "review":
            from vimini.review import handle_channel_response
            handle_channel_response(req_id, result)
        elif method == "setup":
            util.log_info("Agent server setup completed.")
        elif method == "list_models":
            models = result.get("models", [])
            from vimini.models import show_models_list
            show_models_list(models)
        elif method == "chat":
            from vimini.chat import handle_channel_response
            handle_channel_response(req_id, result)
        elif method == "commit":
            from vimini.commit import handle_commit_response
            handle_commit_response(req_id, result)

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
        model = old_util._MODEL
        job_counter = old_util._JOB_COUNTER
        log_file = None
        if old_util._LOGGER and old_util._LOGGER.handlers:
            import logging
            for handler in old_util._LOGGER.handlers:
                if isinstance(handler, logging.FileHandler):
                    log_file = handler.baseFilename
                    break
    except Exception:
        api_key_file = os.path.expanduser('~/.config/gemini.token')
        model = vim.eval("get(g:, 'vimini_model', 'gemini-3.6-flash')")
        job_counter = 0
        log_file = vim.eval("get(g:, 'vimini_log_file', '')")
        if not log_file or vim.eval("get(g:, 'vimini_logging', 'off')") != 'on':
            log_file = None

    # Delete all vimini modules from sys.modules
    modules_to_delete = [m for m in list(sys.modules.keys()) if m.startswith('vimini')]
    for m in modules_to_delete:
        del sys.modules[m]

    # Re-import main and re-initialize
    from vimini import main
    main.initialize(api_key_file=api_key_file, model=model, logfile=log_file)

    # Use the freshly imported util to display the message and restore _JOB_COUNTER
    from vimini import util as new_util
    new_util._JOB_COUNTER = job_counter
    new_util.display_message("Vimini Python modules reloaded.", history=True)

def list_models():
    """
    Lists the available Gemini models.
    """
    util.log_info("list_models()")
    return {
        "jsonrpc": "2.0",
        "id": "list_models",
        "method": "list_models",
        "params": {}
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
        ":ViminiListModels",
        "    Lists all available Gemini models in a new split window.",
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
        "    Opens a guided editor to configure project settings (build/test commands).",
        "",
        ":ViminiReview [-c <git_objects>] [--security] [--save[=<path>]] [{prompt}]",
        "    Reviews code in current buffer or git objects.",
        "    -c <ref>: Review changes in git ref (can be a range for batch review).",
        "    --security: Focus on security vulnerabilities.",
        "    --save: Save reviews to text files (useful with batch review).",
        "    --save=path: Save reviews to a specific directory (defaults to g:vimini_review_path).",
        "",
        ":ViminiDiff",
        "    Shows 'git diff' output in a buffer.",
        "",
        ":ViminiCommit [-n] [-r] [-a] [instruction]",
        "    Generates a commit message and commits changes.",
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
        vim.command(f'file {buf_name}')
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')

    # Update content
    vim.command('setlocal modifiable')
    vim.current.buffer[:] = help_content
    vim.command('setlocal nomodifiable')

    # Highlight handling
    vim.command("try | call clearmatches() | catch | endtry")

    if command_name:
        target = command_name.lstrip(':')
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
