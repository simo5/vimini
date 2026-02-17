import vim
import os, subprocess, shlex, textwrap, json
from google.genai import types
from vimini import util
from vimini.autocomplete import autocomplete, cancel_autocomplete, process_autocomplete_queue
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

# This new function is needed because vimini.vim calls main.logging()
def logging(logfile=None):
    util.set_logging(logfile)

def list_models():
    """
    Lists the available Gemini models.
    """
    util.log_info("list_models()")
    try:
        client = util.get_client()
        if not client:
            return

        # Get the list of models.
        util.display_message("Fetching models...")
        models = client.models.list()
        util.display_message("") # Clear the message

        # Prepare the content for the new buffer.
        model_list = ["Available Models:"]
        for model in models:
            model_list.append(f"- {model.name}")

        # Display the models in a new split window.
        util.new_split()
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
        vim.current.buffer[:] = model_list

    except Exception as e:
        util.display_message(f"Error: {e}", error=True)

def commit(author=None, temperature=None, regenerate=False, refinement=None):
    """
    Generates a commit message. By default, it stages all changes and creates
    a new commit. If `regenerate` is True, it regenerates the message for the
    HEAD commit and amends it.
    """
    util.log_info(f"commit(author='{author}', temperature={temperature}, regenerate={regenerate}, refinement='{refinement}')")
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

            # Get status to find files to add.
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

                    # Handle renames (R) or copies (C) which have a second path
                    if status[0] in ('R', 'C'):
                        orig_end = output.find('\0', i)
                        if orig_end != -1:
                            i = orig_end + 1

                    basename = os.path.basename(path)
                    # Exclude dotfiles, backup files (~), and swap files
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

            # Get the diff of what was just staged.
            staged_diff_cmd = ['git', '-C', repo_path, 'diff', '--staged']
            staged_diff_result = subprocess.run(staged_diff_cmd, capture_output=True, text=True, check=False)

            if staged_diff_result.returncode != 0:
                error_message = staged_diff_result.stderr.strip()
                util.display_message(f"Git error getting staged diff: {error_message}", error=True)
                return

            diff_to_process = staged_diff_result.stdout.strip()

            # Get the diff stat to show in the confirmation popup.
            staged_stat_cmd = ['git', '-C', repo_path, 'diff', '--staged', '--stat']
            staged_stat_result = subprocess.run(staged_stat_cmd, capture_output=True, text=True, check=False)
            if staged_stat_result.returncode == 0:
                diff_stat_output = staged_stat_result.stdout.strip()

        if not diff_to_process:
            message = "HEAD commit is empty. Nothing to regenerate." if regenerate else "No changes to commit."
            util.display_message(message, history=True)
            return

        # Create prompt for AI to generate subject and body.
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

        util.display_message("Generating commit message... (this may take a moment)")

        client = util.get_client()
        if not client:
            msg = "Commit cancelled (client init failed)."
            if not regenerate:
                msg += " Reverting `git add`."
                reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
                subprocess.run(reset_cmd, check=False)
            util.display_message(msg, error=True)
            return

        kwargs = util.create_generation_kwargs(
            contents=prompt,
            temperature=temperature
        )

        response = client.models.generate_content(**kwargs)
        util.display_message("")

        # Parse the response into subject and a raw body.
        response_text = response.text.strip()
        if '---' in response_text:
            parts = response_text.split('---', 1)
            subject = parts[0].strip()
            raw_body = parts[1].strip() if len(parts) > 1 else ""
        else:  # Fallback if model doesn't follow instructions.
            lines = response_text.split('\n')
            subject = lines[0].strip()
            raw_body = '\n'.join(lines[1:]).strip()

        # Wrap the body so that no line is longer than 78 characters.
        body = ""
        if raw_body:
            wrapped_lines = []
            for line in raw_body.split('\n'):
                # Preserve blank lines for paragraph separation. textwrap.wrap()
                # would otherwise discard them.
                if not line.strip():
                    wrapped_lines.append('')
                else:
                    wrapped_lines.extend(textwrap.wrap(line, width=78))
            body = '\n'.join(wrapped_lines)


        if not subject:
            msg = "Failed to generate a commit message."
            if not regenerate:
                msg += " Reverting `git add`."
                reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
                subprocess.run(reset_cmd, check=False)
            util.display_message(msg, error=True)
            return

        # Show the generated message in a popup for review and confirmation.
        popup_content = [f"Subject: {subject}", ""]
        if body:
            popup_content.extend(body.split('\n'))

        if diff_stat_output:
            stat_header = '--- Files in commit ---' if regenerate else '--- Staged files ---'
            popup_content.extend(['', stat_header])
            popup_content.extend(diff_stat_output.split('\n'))

        popup_title = ' Regenerate Commit Message ' if regenerate else ' Commit Message '
        popup_question = 'Amend HEAD with this message? [y/n]' if regenerate else 'Commit with this message? [y/n]'
        popup_content.extend(['', '---', popup_question])


        # The str() representation of a Python dict is compatible with Vimscript's
        # dict syntax, which is required for vim.eval(). For popup_create, the
        # value 0 for 'line' and 'col' centers the popup.
        popup_options = {
            'title': popup_title, 'line': 0, 'col': 0,
            'minwidth': 50, 'maxwidth': 80,
            'padding': [1, 2, 1, 2], 'border': [1, 1, 1, 1],
            'borderchars': ['─', '│', '─', '│', '╭', '╮', '╯', '╰'],
            'close': 'none', 'zindex': 200,
        }
        # Use vim.eval to call Vim's popup_create function.
        popup_id = vim.eval(f"popup_create({popup_content}, {popup_options})")
        # Show the popup
        vim.command("redraw!")

        # Capture a single character for confirmation.
        commit_confirmed = False
        try:
            # We convert it to a char to check for 'y' or 'Y'.
            answer_code = vim.eval('getchar()')
            # Ensure answer_code is a string that can be converted to an integer.
            # If not (e.g., for special keys), it is not an affirmative answer.
            answer_char = chr(int(answer_code))
            if answer_char.lower() == 'y':
                commit_confirmed = True
        except (vim.error, ValueError, TypeError): # Catches Ctrl-C and non-integer return values.
            pass # commit_confirmed remains False
        finally:
            # Ensure the popup is always closed, no matter what key was pressed.
            vim.eval(f"popup_close({popup_id})")
            # Redraw to clear any screen artifacts from the popup.
            vim.command("redraw!")

        # If user cancelled, revert the staging and exit.
        if not commit_confirmed:
            if regenerate:
                util.display_message("Amend cancelled.", error=True)
            else:
                util.display_message("Commit cancelled. Reverting `git add`.", error=True)
                reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
                subprocess.run(reset_cmd, check=False)
            return

        util.log_info("Commit Message accepted")

        # Construct the commit command with subject, body, and sign-off.
        commit_cmd = ['git', '-C', repo_path, 'commit']
        if regenerate:
            commit_cmd.append('--amend')
        commit_cmd.extend(['-s', '-m', subject])

        if body:
            commit_cmd.extend(['-m', body])
        if author:
            commit_cmd.extend(['-m', '', '-m', author]) # Blank line before trailer.

        action = "Amending" if regenerate else "Committing"
        util.display_message(f"{action} with subject: {subject}", history=True)
        vim.command("redraw")

        commit_result = subprocess.run(commit_cmd, capture_output=True, text=True, check=False)

        if commit_result.returncode == 0:
            success_message = commit_result.stdout.strip().split('\n')[0]
            action_past = "Amend" if regenerate else "Commit"
            util.display_message(f"{action_past} successful: {success_message}", history=True)
        else:
            error_message = (commit_result.stderr or commit_result.stdout).strip()
            action_past = "amend" if regenerate else "commit"
            util.display_message(f"Git {action_past} failed: {error_message}", error=True)

    except FileNotFoundError:
        util.display_message("Error: `git` command not found. Is it in your PATH?", error=True)
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
        ":ViminiChat {prompt}",
        "    Sends a prompt to the configured Gemini model and displays the response.",
        "    If no prompt is provided, opens the chat buffer for interactive mode.",
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
        ":ViminiReview [-c <git_objects>] [--security] [--save] [{prompt}]",
        "    Reviews code in current buffer or git objects.",
        "    -c <ref>: Review changes in git ref.",
        "    --security: Focus on security.",
        "    --save: Save reviews to files.",
        "",
        ":ViminiDiff",
        "    Shows 'git diff' output in a buffer.",
        "",
        ":ViminiCommit [-n] [-r]",
        "    Generates a commit message and commits changes.",
        "    -n: No co-author trailer.",
        "    -r: Regenerate/Amend HEAD.",
        "",
        ":ViminiFiles",
        "    Manages remote files uploaded to Gemini.",
        "",
        ":ViminiToggleAutocomplete [on|off]",
        "    Toggles real-time ghost-text autocomplete.",
        "",
        ":ViminiRipGrep {regex} {prompt}",
        "    Search with ripgrep and modify results with AI.",
        "",
        ":ViminiRipGrepApply",
        "    Apply changes from ViminiRipGrep buffer.",
        "",
        ":ViminiHelp [command]",
        "    Shows this help. Optionally jumps to [command].",
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