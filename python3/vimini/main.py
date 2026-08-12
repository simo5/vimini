import vim
import os, subprocess, shlex, textwrap, json, tempfile, time
from google.genai import types
from vimini import util
from vimini.util import process_queue, get_model_name
from vimini.autocomplete import autocomplete, cancel_autocomplete
from vimini.code import code, show_diff, apply_code
from vimini.review import review
from vimini.ripgrep import command as ripgrep_command
from vimini.ripgrep import apply as ripgrep_apply
from vimini.chat import chat
from vimini.context import context_files_command, toggle_context_file, show_context_lists, confirm_context_files, files_command

def initialize(api_key, model, logfile=None):
    """
    Initializes the plugin with the user's API key, model name, and
    optional logfile path.
    This function is called from the plugin's Vimscript entry point.
    """
    util._API_KEY = api_key
    util._MODEL = model
    util._GENAI_CLIENT = None # Reset client if key/model changes.
    util.set_logging(logfile)
    if not util._API_KEY:
        util.display_message("API key not found. Please set g:vimini_api_key or store it in ~/.config/gemini.token.", error=True)

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
            "api_key": util._API_KEY,
            "model": util._MODEL,
            "temperature": temperature
        }
    }
    return _send_channel_request(req, silent=False)

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

def handle_commit_response(result):
    text = result.get("text", "")
    repo_path = result.get("repo_path", "")
    diff_stat_output = result.get("diff_stat_output", "")
    regenerate = bool(result.get("regenerate", False))
    assistant = bool(result.get("assistant", True))

    response_text = text.strip()
    if '---' in response_text:
        parts = response_text.split('---', 1)
        subject = parts[0].strip()
        raw_body = parts[1].strip() if len(parts) > 1 else ""
    else:  # Fallback if model doesn't follow instructions.
        lines = response_text.split('\n')
        subject = lines[0].strip()
        raw_body = '\n'.join(lines[1:]).strip()

    body = ""
    if raw_body:
        wrapped_lines = []
        for line in raw_body.split('\n'):
            if not line.strip():
                wrapped_lines.append('')
            else:
                wrapped_lines.extend(textwrap.wrap(line, width=78))
        body = '\n'.join(wrapped_lines)

    if not subject:
        msg = "Failed to generate a commit message."
        if not regenerate and repo_path:
            msg += " Reverting `git add`."
            reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
            subprocess.run(reset_cmd, check=False)
        util.display_message(msg, error=True)
        return

    with tempfile.NamedTemporaryFile(mode="w+", delete=False, encoding="utf-8", suffix=".gitcommit") as f:
        tmp_filename = f.name

    util.new_split()
    vim.command(f"edit {tmp_filename.replace(' ', '\\ ')}")
    vim.command("setlocal filetype=gitcommit")
    vim.command("setlocal bufhidden=wipe")

    buffer_content = [subject, ""]
    if body:
        buffer_content.extend(body.split('\n'))

    if diff_stat_output:
        stat_header = '# --- Files in commit ---' if regenerate else '# --- Staged files ---'
        buffer_content.extend(['', stat_header])
        for line in diff_stat_output.split('\n'):
            buffer_content.append(f"# {line}")

    vim.current.buffer[:] = buffer_content

    safe_tmp = tmp_filename.replace("'", "''")
    safe_repo = repo_path.replace("'", "''")
    vim.command(f"let b:vimini_commit_tmp_file = '{safe_tmp}'")
    vim.command(f"let b:vimini_commit_repo_path = '{safe_repo}'")
    vim.command(f"let b:vimini_commit_regenerate = {1 if regenerate else 0}")
    vim.command(f"let b:vimini_commit_assistant = {1 if assistant else 0}")

    vim.command("autocmd BufWipeout <buffer> py3 from vimini import main; main._finalize_commit()")

    util.display_message("Review the commit message. Save and close the buffer to commit, or close without saving to abort.", history=True)
    vim.command("redraw!")

def handle_channel_message(msg):
    """
    Handles JSON channel messages received from the agent server via Vim channel.
    """
    util.log_info(f"Received channel message: {msg}")
    if not isinstance(msg, dict):
        return
    error = msg.get("error")
    if error:
        err_msg = error.get("message", "Unknown error") if isinstance(error, dict) else str(error)
        util.display_message(f"Error: {err_msg}", error=True)
        return
    result = msg.get("result")
    if isinstance(result, dict):
        method = result.get("method")
        if method == "autocomplete":
            from vimini.autocomplete import handle_channel_response
            handle_channel_response(result)
        elif method == "setup":
            util.log_info("Agent server setup completed.")
        elif method == "list_models":
            models = result.get("models", [])
            util.display_message("")
            model_list = ["Available Models:"]
            for model in models:
                model_list.append(f"- {model}")

            util.new_split()
            vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
            vim.current.buffer[:] = model_list
        elif method == "chat":
            from vimini.chat import handle_channel_response
            handle_channel_response(result)
        elif method == "commit":
            handle_commit_response(result)

# This new function is needed because vimini.vim calls main.logging()
def logging(logfile=None):
    util.set_logging(logfile)

def reload_vimini():
    """
    Reloads the vimini python modules to pick up changes from disk.
    Also re-initializes the plugin to preserve the API key and settings.
    """
    import sys
    import os
    import vim

    # Save initialization parameters before deleting modules
    try:
        from vimini import util as old_util
        api_key = old_util._API_KEY
        model = old_util._MODEL
        log_file = None
        if old_util._LOGGER and old_util._LOGGER.handlers:
            import logging
            for handler in old_util._LOGGER.handlers:
                if isinstance(handler, logging.FileHandler):
                    log_file = handler.baseFilename
                    break
    except Exception:
        api_key = vim.eval("get(g:, 'vimini_api_key', '')")
        if not api_key:
            token_path = os.path.expanduser('~/.config/gemini.token')
            if os.path.exists(token_path):
                try:
                    with open(token_path, 'r') as f:
                        api_key = f.read().strip()
                except Exception:
                    pass
        model = vim.eval("get(g:, 'vimini_model', 'gemini-2.5-flash')")
        log_file = vim.eval("get(g:, 'vimini_log_file', '')")
        if not log_file or vim.eval("get(g:, 'vimini_logging', 'off')") != 'on':
            log_file = None

    # Delete all vimini modules from sys.modules
    modules_to_delete = [m for m in list(sys.modules.keys()) if m.startswith('vimini')]
    for m in modules_to_delete:
        del sys.modules[m]

    # Re-import main and re-initialize
    from vimini import main
    main.initialize(api_key=api_key, model=model, logfile=log_file)

    # Use the freshly imported util to display the message
    from vimini import util as new_util
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

def commit(assistant=True, temperature=None, regenerate=False, refinement=None):
    """
    Generates a commit message. By default, it stages all changes and creates
    a new commit. If `regenerate` is True, it regenerates the message for the
    HEAD commit and amends it.
    Offloads commit message generation to the agent server.
    """
    util.log_info(f"commit(assistant={assistant}, temperature={temperature}, regenerate={regenerate}, refinement='{refinement}')")
    try:
        repo_path = util.get_git_repo_root()
        if not repo_path:
            return # Error handled by helper

        diff_to_process = ""
        diff_stat_output = ""

        if regenerate:
            util.display_message("Getting diff from HEAD...")
            diff_cmd = ['git', '-C', repo_path, 'show', '--format=']
            diff_result = subprocess.run(diff_cmd, capture_output=True, text=True, check=False)

            if diff_result.returncode != 0:
                error_message = (diff_result.stderr or "git show HEAD failed.").strip()
                util.display_message(f"Git error: {error_message}", error=True)
                return
            diff_to_process = diff_result.stdout.strip()

            stat_cmd = ['git', '-C', repo_path, 'show', '--format=', '--stat']
            stat_result = subprocess.run(stat_cmd, capture_output=True, text=True, check=False)
            if stat_result.returncode == 0:
                diff_stat_output = stat_result.stdout.strip()
        else:
            # Stage changes with filtering (exclude dotfiles and swap/backup files)
            util.display_message("Staging changes...")

            status_cmd = ['git', '-C', repo_path, 'status', '-z', '--porcelain']
            status_result = subprocess.run(status_cmd, capture_output=True, text=True, check=False)

            files_to_add = []
            if status_result.returncode == 0:
                output = status_result.stdout
                i = 0
                n = len(output)
                while i < n:
                    if i + 3 > n: break
                    status = output[i:i+2]
                    path_start = i + 3
                    path_end = output.find('\0', path_start)
                    if path_end == -1: break

                    path = output[path_start:path_end]
                    i = path_end + 1

                    if status[0] in ('R', 'C'):
                        orig_end = output.find('\0', i)
                        if orig_end != -1:
                            i = orig_end + 1

                    basename = os.path.basename(path)
                    if (basename.startswith('.') or
                        basename.endswith('~') or
                        basename.endswith('.swp') or
                        basename.endswith('.swo') or
                        basename.endswith('.review.txt')):
                        continue

                    files_to_add.append(path)

            if files_to_add:
                add_cmd = ['git', '-C', repo_path, 'add', '--'] + files_to_add
                add_result = subprocess.run(add_cmd, capture_output=True, text=True, check=False)

                if add_result.returncode != 0:
                    error_message = (add_result.stderr or add_result.stdout).strip()
                    util.display_message(f"Git add failed: {error_message}", error=True)
                    return

            util.display_message("")

            staged_diff_cmd = ['git', '-C', repo_path, 'diff', '--staged']
            staged_diff_result = subprocess.run(staged_diff_cmd, capture_output=True, text=True, check=False)

            if staged_diff_result.returncode != 0:
                error_message = staged_diff_result.stderr.strip()
                util.display_message(f"Git error getting staged diff: {error_message}", error=True)
                return

            diff_to_process = staged_diff_result.stdout.strip()

            staged_stat_cmd = ['git', '-C', repo_path, 'diff', '--staged', '--stat']
            staged_stat_result = subprocess.run(staged_stat_cmd, capture_output=True, text=True, check=False)
            if staged_stat_result.returncode == 0:
                diff_stat_output = staged_stat_result.stdout.strip()

        if not diff_to_process:
            message = "HEAD commit is empty. Nothing to regenerate." if regenerate else "No changes to commit."
            util.display_message(message, history=True)
            return

        prompt = (
            "Based on the following git diff, generate a commit message with a subject and a body.\n\n"
            "RULES:\n"
            "1. The subject must be a single line, 50 characters or less, and summarize the change.\n"
            "2. Do not add any prefixes like 'feat:' or 'fix:' to the subject.\n"
            "3. The body should be a brief description of the changes, explaining the 'what' and 'why'.\n"
            "4. Separate the subject and body with '---' on its own line.\n"
            "5. Only output the raw text, with no extra explanations or markdown."
        )

        if refinement:
            prompt += f"\n\nADDITIONAL INSTRUCTIONS:\n{refinement}"

        prompt += (
            "\n\n--- GIT DIFF ---\n"
            f"{diff_to_process}\n"
            "--- END GIT DIFF ---"
        )

        util.display_message("Generating commit message via agent server... (this may take a moment)")

        req = {
            "jsonrpc": "2.0",
            "id": "commit",
            "method": "commit",
            "params": {
                "prompt": prompt,
                "temperature": temperature,
                "repo_path": repo_path,
                "diff_stat_output": diff_stat_output,
                "regenerate": regenerate,
                "assistant": assistant
            }
        }

        if not _send_channel_request(req):
            msg = "Commit cancelled (sending request to agent server failed)."
            if not regenerate:
                msg += " Reverting `git add`."
                reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
                subprocess.run(reset_cmd, check=False)
            util.display_message(msg, error=True)

    except FileNotFoundError:
        util.display_message("Error: `git` command not found. Is it in your PATH?", error=True)
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)

def _finalize_commit():
    """Finalizes the commit after the user closes the commit message buffer."""
    util.log_info("Finalizing commit")
    try:
        tmp_filename = vim.eval("get(b:, 'vimini_commit_tmp_file', '')")
        repo_path = vim.eval("get(b:, 'vimini_commit_repo_path', '')")
        regenerate = int(vim.eval("get(b:, 'vimini_commit_regenerate', 0)"))
        assistant = int(vim.eval("get(b:, 'vimini_commit_assistant', 0)"))
    except Exception as e:
        util.log_info(f"Failed to read commit variables: {e}")
        return

    if not tmp_filename or not os.path.exists(tmp_filename):
        util.log_info(f"Failed to find commit log file")
        return

    try:
        with open(tmp_filename, 'r', encoding='utf-8') as f:
            content = f.read()
        os.remove(tmp_filename)
    except Exception as e:
        util.display_message(f"Error reading commit message: {e}", error=True)
        return

    # Check if all lines are comments or empty
    has_non_comment = False
    for line in content.split('\n'):
        if line.strip() and not line.strip().startswith('#'):
            has_non_comment = True
            break

    if not has_non_comment:
        if regenerate:
            util.display_message("Amend cancelled (no commit message saved).", error=True)
        else:
            util.display_message("Commit cancelled (no commit message saved). Reverting `git add`.", error=True)
            reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
            subprocess.run(reset_cmd, check=False)
        return

    util.log_info("Commit Message accepted")

    commit_cmd = ['git', '-C', repo_path, 'commit', '-s', '--cleanup=strip']
    if regenerate:
        commit_cmd.append('--amend')

    if assistant:
        trailer = f"Assisted-by: Gemini:{get_model_name()}"
        if trailer not in content:
            content += f"\n\n{trailer}\n"

    commit_cmd.extend(['-F', '-'])

    action = "Amending" if regenerate else "Committing"
    util.display_message(f"{action}...", history=True)
    vim.command("redraw")

    try:
        commit_result = subprocess.run(commit_cmd, input=content, capture_output=True, text=True, check=False)

        if commit_result.returncode == 0:
            success_message = commit_result.stdout.strip().split('\n')[0]
            action_past = "Amend" if regenerate else "Commit"
            util.display_message(f"{action_past} successful: {success_message}", history=True)
        else:
            error_message = (commit_result.stderr or commit_result.stdout).strip()
            action_past = "amend" if regenerate else "commit"
            util.display_message(f"Git {action_past} failed: {error_message}", error=True)
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)

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
        ":ViminiCommit [-n] [-r] [instruction]",
        "    Generates a commit message and commits changes.",
        "    -n: No co-author trailer.",
        "    -r: Regenerate/Amend HEAD.",
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
