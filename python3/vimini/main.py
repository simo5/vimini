import vim
import os, subprocess, shlex, textwrap, json
from google.genai import types
from vimini import util
from vimini.autocomplete import autocomplete, cancel_autocomplete, process_autocomplete_queue
from vimini.code import code, show_diff, apply_code
from vimini.ripgrep import command as ripgrep_command
from vimini.ripgrep import apply as ripgrep_apply

# Global variable to hold the list of pending context files while the
# context file manager is open.
_VIMINI_PENDING_CONTEXT_FILES = None

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

def chat(prompt):
    """
    Sends a prompt to the Gemini API and displays the response in a new buffer.
    """
    util.log_info(f"chat({prompt})")
    try:
        client = util.get_client()
        if not client:
            return

        # Immediately open a new split window for the chat.
        util.new_split()
        vim.command('file Vimini Chat')
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')

        # Display the prompt in the new buffer.
        vim.current.buffer[:] = [f"Q: {prompt}", "---", "A:"]
        vim.command('normal! G') # Move cursor to the end to prepare for the answer
        vim.command("redraw")

        # Send the prompt and get the response.
        util.display_message("Processing...")
        kwargs = util.create_generation_kwargs(contents=prompt)
        response = client.models.generate_content(**kwargs)
        util.display_message("") # Clear the thinking message
        vim.current.buffer.append(response.text.split('\n'))

    except Exception as e:
        util.display_message(f"Error: {e}", error=True)

def review(prompt, git_objects=None, verbose=False, temperature=None):
    """
    Sends content to the Gemini API for a code review.
    If git_objects are provided, it reviews the output of `git show <objects>`.
    Otherwise, it reviews the content of the current buffer.
    The review is displayed in a new buffer, streaming thoughts if verbose.
    """
    util.log_info(f"review({prompt}, git_objects='{git_objects}', verbose={verbose}, temperature={temperature})")
    try:
        client = util.get_client()
        if not client:
            return

        review_content = ""
        content_source_description = ""

        if git_objects:
            # Handle review of git objects
            repo_path = util.get_git_repo_root()
            if not repo_path:
                return # Error message is handled by util.get_git_repo_root()

            # Security Hardening: Prevent command injection via git flags.
            # The user should only provide git objects (hashes, branches, etc.), not options.
            objects_to_show = shlex.split(git_objects)
            for obj in objects_to_show:
                if obj.startswith('-'):
                    util.display_message("Security error: Git options (like flags starting with '-') are not allowed.", error=True)
                    return

            cmd = ['git', '-C', repo_path, 'show'] + objects_to_show

            util.display_message(f"Running git show {git_objects}... ")
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if result.returncode != 0:
                error_message = (result.stderr or "git show failed.").strip()
                util.display_message(f"Git error: {error_message}", error=True)
                return
            util.display_message("") # Clear message

            review_content = result.stdout

            # As requested, open a new buffer with the git show output, which becomes the context
            util.new_split()
            # Truncate for display if the object string is too long
            display_objects = (git_objects[:40] + '..') if len(git_objects) > 40 else git_objects
            vim.command(f'file Vimini Git Review Target: {display_objects}')
            vim.command('setlocal buftype=nofile filetype=diff noswapfile')
            vim.current.buffer[:] = review_content.split('\n')
            vim.command('normal! 1G') # Go to top of new buffer

            content_source_description = f"the output of `git show {git_objects}`"
        else:
            # Handle review of the current buffer (original behavior)
            review_content = "\n".join(vim.current.buffer[:])
            original_filetype = vim.eval('&filetype') or 'text'
            content_source_description = f"the following {original_filetype} code"

        if not review_content.strip():
            util.display_message("Nothing to review.", history=True)
            return

        # Construct the full prompt for the API.
        full_prompt = (
            f"Please review {content_source_description} for potential issues, "
            "improvements, best practices, and any possible bugs. "
            "Provide a concise summary and actionable suggestions.\n\n"
            "--- CONTENT TO REVIEW ---\n"
            f"{review_content}\n"
            "--- END CONTENT TO REVIEW ---"
            f"\n{prompt}\n"
        )

        thoughts_buffer = None
        if verbose:
            # Create the Vimini Thoughts buffer before calling the model.
            util.new_split()
            vim.command('file Vimini Thoughts')
            vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
            thoughts_buffer = vim.current.buffer
            thoughts_buffer[:] = ['']

        # Create the Vimini Review buffer. This becomes the active window.
        util.new_split()
        vim.command('file Vimini Review')
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
        review_buffer = vim.current.buffer # Reference the new review buffer
        review_buffer[:] = ['']

        # Get window numbers for faster switching during streaming.
        thoughts_win_nr = None
        if verbose:
            thoughts_win_nr = vim.eval(f"bufwinnr({thoughts_buffer.number})")
        review_win_nr = vim.eval(f"bufwinnr({review_buffer.number})")

        # Display a Processing.. message so users know they have to wait
        util.display_message("Processing...")

        # Set up the API call arguments
        kwargs = util.create_generation_kwargs(
            contents=full_prompt,
            temperature=temperature,
            verbose=verbose
        )

        # Use generate_content_stream()
        response_stream = client.models.generate_content_stream(**kwargs)

        for chunk in response_stream:
            if not chunk.candidates:
                continue
            for part in chunk.candidates[0].content.parts:
                if not part.text:
                    continue

                is_thought = hasattr(part, 'thought') and part.thought
                # If we're not in verbose mode, we don't care about thought parts.
                if is_thought and not verbose:
                    continue

                target_buffer = thoughts_buffer if is_thought else review_buffer
                # This should not be None due to the check above, but for safety.
                if not target_buffer:
                    continue

                # Switch to the window displaying the buffer being updated.
                target_win_nr = thoughts_win_nr if is_thought else review_win_nr
                if int(target_win_nr) > 0:
                    vim.command(f"{target_win_nr}wincmd w")

                # Split incoming text by newlines to handle chunks that span multiple lines
                new_lines = part.text.split('\n')

                # Append the first part of the new text to the current last line in the buffer
                target_buffer[-1] += new_lines[0]

                # If the chunk contained one or more newlines, add the rest as new lines
                if len(new_lines) > 1:
                    target_buffer.append(new_lines[1:])

                # Move cursor to the end and scroll view to keep the last line visible.
                vim.command('normal! Gz-')
                util.display_message("Processing...")

        util.display_message("") # Clear the thinking message

    except FileNotFoundError:
        util.display_message("Error: `git` command not found. Is it in your PATH?", error=True)
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)

def commit(author=None, temperature=None):
    """
    Generates a detailed commit message (subject and body) using the Gemini
    API based on all current changes. It stages everything, shows the generated
    message in a popup for review, and then commits, optionally with a
    'Co-authored-by' trailer.
    """
    util.log_info(f"commit(author='{author}', temperature={temperature})")
    try:
        repo_path = util.get_git_repo_root()
        if not repo_path:
            return # Error handled by helper

        # Stage all changes to get a complete diff for the commit message.
        util.display_message("Staging all changes... (git add .)")
        add_cmd = ['git', '-C', repo_path, 'add', '.']
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

        if not diff_to_process:
            util.display_message("No changes to commit.", history=True)
            return

        # Get the diff stat to show in the confirmation popup.
        staged_stat_cmd = ['git', '-C', repo_path, 'diff', '--staged', '--stat']
        staged_stat_result = subprocess.run(staged_stat_cmd, capture_output=True, text=True, check=False)
        diff_stat_output = ""
        if staged_stat_result.returncode == 0:
            diff_stat_output = staged_stat_result.stdout.strip()

        # Create prompt for AI to generate subject and body.
        prompt = (
            "Based on the following git diff, generate a commit message with a subject and a body.\n\n"
            "RULES:\n"
            "1. The subject must be a single line, 50 characters or less, and summarize the change.\n"
            "2. Do not add any prefixes like 'feat:' or 'fix:' to the subject.\n"
            "3. The body should be a brief description of the changes, explaining the 'what' and 'why'.\n"
            "4. Separate the subject and body with '---' on its own line.\n"
            "5. Only output the raw text, with no extra explanations or markdown.\n\n"
            "--- GIT DIFF ---\n"
            f"{diff_to_process}\n"
            "--- END GIT DIFF ---"
        )

        util.display_message("Generating commit message... (this may take a moment)")

        client = util.get_client()
        if not client:
            util.display_message("Commit cancelled (client init failed). Reverting `git add`.", error=True)
            reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
            subprocess.run(reset_cmd, check=False)
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
            util.display_message("Failed to generate a commit message. Reverting `git add`.", error=True)
            reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
            subprocess.run(reset_cmd, check=False)
            return

        # Show the generated message in a popup for review and confirmation.
        popup_content = [f"Subject: {subject}", ""]
        if body:
            popup_content.extend(body.split('\n'))

        if diff_stat_output:
            popup_content.extend(['', '--- Staged files ---'])
            popup_content.extend(diff_stat_output.split('\n'))

        popup_content.extend(['', '---', 'Commit with this message? [y/n]'])


        # The str() representation of a Python dict is compatible with Vimscript's
        # dict syntax, which is required for vim.eval(). For popup_create, the
        # value 0 for 'line' and 'col' centers the popup.
        popup_options = {
            'title': ' Commit Message ', 'line': 0, 'col': 0,
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
            util.display_message("Commit cancelled. Reverting `git add`.", error=True)
            reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
            subprocess.run(reset_cmd, check=False)
            return

        util.log_info("Commit Message accepted")

        # Construct the commit command with subject, body, and sign-off.
        commit_cmd = ['git', '-C', repo_path, 'commit', '-s', '-m', subject]
        if body:
            commit_cmd.extend(['-m', body])
        if author:
            commit_cmd.extend(['-m', '', '-m', author]) # Blank line before trailer.

        util.display_message(f"Committing with subject: {subject}", history=True)
        vim.command("redraw")

        commit_result = subprocess.run(commit_cmd, capture_output=True, text=True, check=False)

        if commit_result.returncode == 0:
            success_message = commit_result.stdout.strip().split('\n')[0]
            util.display_message(f"Commit successful: {success_message}", history=True)
        else:
            error_message = (commit_result.stderr or commit_result.stdout).strip()
            util.display_message(f"Git commit failed: {error_message}", error=True)

    except FileNotFoundError:
        util.display_message("Error: `git` command not found. Is it in your PATH?", error=True)
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)

def _draw_context_files_listing(target_path, project_root, context_files_list):
    """
    Generates the list of lines for the context files buffer.
    """
    # 1. Create a set of absolute paths for files in context for quick lookups.
    context_files_abs = set()
    for f in context_files_list:
        path = os.path.expanduser(f)
        if not os.path.isabs(path):
            path = os.path.join(project_root, path)
        context_files_abs.add(os.path.normpath(path))

    # 2. Prepare buffer header
    buffer_lines = [
        "| Vimini Context Files",
        "|------------------------------------------",
        "| <CR>: toggle/enter | l: list | q: close",
        "| C: in context | >: directory",
        "",
        "> .."
    ]

    # 3. Get directory listing
    dirs_to_ignore = {'.git', '__pycache__', 'node_modules', '.venv', 'target'}
    dirs, files = [], []
    try:
        for name in os.listdir(target_path):
            if name in dirs_to_ignore:
                continue
            full_path = os.path.join(target_path, name)
            if os.path.isdir(full_path):
                dirs.append(name)
            else:
                files.append(name)
    except OSError as e:
        util.display_message(f"Error reading directory '{target_path}': {e}", error=True)
        return None

    # 4. Format and append directory and file lines
    for d in sorted(dirs):
        buffer_lines.append(f"> {d}")
    for f in sorted(files):
        full_path = os.path.join(target_path, f)
        is_in_context = os.path.normpath(full_path) in context_files_abs
        prefix = "C " if is_in_context else "  "
        buffer_lines.append(f"{prefix}{f}")

    return buffer_lines

def context_files_command():
    """
    Shows a new buffer with a file explorer to manage g:context_files.
    """
    util.log_info("context_files_command()")
    global _VIMINI_PENDING_CONTEXT_FILES
    try:
        # 1. Get paths and existing context files
        current_path = os.path.abspath(vim.eval('getcwd()'))
        project_root = util.get_git_repo_root() or current_path

        try:
            initial_context_files = vim.eval("get(g:, 'context_files', [])")
            if not isinstance(initial_context_files, list):
                initial_context_files = []
        except vim.error:
            initial_context_files = []

        # Store the pending list in the global python variable.
        _VIMINI_PENDING_CONTEXT_FILES = initial_context_files

        # 2. Get directory listing using the helper
        buffer_lines = _draw_context_files_listing(current_path, project_root, _VIMINI_PENDING_CONTEXT_FILES)
        if buffer_lines is None:
            return # Error was displayed by helper

        # 3. Create and populate the new buffer
        util.new_split()
        vim.command('file ViminiContextFiles')
        buf = vim.current.buffer
        buf[:] = buffer_lines

        # 4. Set buffer options
        vim.command('setlocal buftype=nofile noswapfile nomodifiable')
        vim.command(f"let b:vimini_context_root = '{project_root}'")
        vim.command(f"let b:vimini_context_path = '{current_path}'")

        # 5. Set up key mappings and autocmd for close confirmation
        vim.command("nnoremap <buffer> <silent> <CR> :py3 from vimini.main import toggle_context_file; toggle_context_file()<CR>")
        vim.command("nnoremap <buffer> <silent> l :py3 from vimini.main import show_context_lists; show_context_lists()<CR>")
        vim.command("nnoremap <buffer> <silent> q :q<CR>")
        vim.command("autocmd BufUnload <buffer> :py3 from vimini.main import confirm_context_files; confirm_context_files()")
        # Move cursor past header to the first file/directory entry.
        vim.current.window.cursor = (6, 0)
        vim.command('setlocal readonly')

    except Exception as e:
        util.display_message(f"Error managing context files: {e}", error=True)

def toggle_context_file():
    """
    Called by <Enter> mapping in the ViminiContextFiles buffer.
    Adds/removes a file from g:context_files, or navigates directories.
    """
    global _VIMINI_PENDING_CONTEXT_FILES
    try:
        buf = vim.current.buffer
        win = vim.current.window
        line_num, col = win.cursor
        line = buf[line_num - 1]

        # Ignore empty lines or header/comment lines
        if not line.strip() or line.strip().startswith('|'):
            return

        current_path = vim.eval("get(b:, 'vimini_context_path', '')")
        project_root = vim.eval("get(b:, 'vimini_context_root', '')")
        if not current_path or not project_root:
            util.display_message("Error: Context buffer variables not set.", error=True)
            return

        # --- Directory Navigation Logic ---
        if line.startswith('> '):
            dir_name = line[2:].strip()
            if dir_name == '..':
                new_path = os.path.dirname(current_path)
            else:
                new_path = os.path.join(current_path, dir_name)

            # If not a valid directory, stay in the current one to redraw it.
            if not os.path.isdir(new_path):
                new_path = current_path

            new_path = os.path.abspath(new_path)

            # Re-render the buffer for the new path
            context_files_list = _VIMINI_PENDING_CONTEXT_FILES
            if not isinstance(context_files_list, list):
                # This should not happen if context_files_command() was called.
                util.display_message("Error: Pending context files list is not available.", error=True)
                return

            buffer_lines = _draw_context_files_listing(new_path, project_root, context_files_list)
            if buffer_lines is None:
                return # Error displayed by helper

            vim.command('setlocal modifiable')
            buf[:] = buffer_lines
            vim.command(f"let b:vimini_context_path = '{new_path}'")
            vim.command('setlocal readonly')
            win.cursor = (6, 0)
            return

        # --- File Toggling Logic ---
        if len(line) < 3: return
        prefix = line[:2]
        file_name = line[2:].strip()

        full_path_on_line = os.path.normpath(os.path.join(current_path, file_name))

        # If not a valid file, redraw current directory and do nothing else.
        if not os.path.isfile(full_path_on_line):
            context_files_list = _VIMINI_PENDING_CONTEXT_FILES
            if not isinstance(context_files_list, list):
                util.display_message("Error: Pending context files list is not available.", error=True)
                return

            buffer_lines = _draw_context_files_listing(current_path, project_root, context_files_list)
            if buffer_lines is None:
                return # Error displayed by helper

            vim.command('setlocal modifiable')
            buf[:] = buffer_lines
            vim.command('setlocal readonly')
            win.cursor = (6, 0) # cursor to top after redraw
            return

        is_in_context = (prefix == "C ")

        relative_path_for_storage = os.path.relpath(full_path_on_line, project_root)

        context_files_list = _VIMINI_PENDING_CONTEXT_FILES
        if not isinstance(context_files_list, list):
            util.display_message("Error: Pending context files list is not available.", error=True)
            return

        new_list = []
        if is_in_context:
            # Remove it
            for f in context_files_list:
                path_in_list = os.path.expanduser(f)
                if not os.path.isabs(path_in_list):
                    path_in_list = os.path.join(project_root, path_in_list)
                if os.path.normpath(path_in_list) != full_path_on_line:
                    new_list.append(f)
            new_prefix = "  "
        else:
            # Add it
            new_list = context_files_list
            is_already_present = False
            for f in context_files_list:
                path_in_list = os.path.expanduser(f)
                if not os.path.isabs(path_in_list):
                    path_in_list = os.path.join(project_root, path_in_list)
                if os.path.normpath(path_in_list) == full_path_on_line:
                    is_already_present = True
                    break
            if not is_already_present:
                new_list.append(relative_path_for_storage)
            new_prefix = "C "

        _VIMINI_PENDING_CONTEXT_FILES = new_list

        vim.command('setlocal modifiable')
        buf[line_num - 1] = f"{new_prefix}{file_name}"
        vim.command('setlocal readonly')
        vim.command("redraw")
        win.cursor = (line_num, col)

    except Exception as e:
        vim.command(f"echoerr '[Vimini] Error toggling context file: {str(e).replace("'", "''")}'")

def show_context_lists():
    """
    Called by 'l' mapping in the ViminiContextFiles buffer.
    Shows a popup with the current and pending context file lists.
    """
    global _VIMINI_PENDING_CONTEXT_FILES
    popup_id = 0
    try:
        # Get active context files
        try:
            active_files = vim.eval("get(g:, 'context_files', [])")
            if not isinstance(active_files, list):
                active_files = []
        except vim.error:
            active_files = []

        # Get pending context files
        pending_files = _VIMINI_PENDING_CONTEXT_FILES
        if pending_files is None:
            # This shouldn't happen if the buffer is open, but as a safeguard
            pending_files = active_files

        # Build popup content
        popup_content = ["--- Active Context Files ---"]
        if active_files:
            popup_content.extend(sorted(active_files))
        else:
            popup_content.append("(none)")

        # Compare and add pending files if different
        if sorted(active_files) != sorted(pending_files):
            popup_content.append("")
            popup_content.append("--- Pending Context Files (unsaved) ---")
            if pending_files:
                popup_content.extend(sorted(pending_files))
            else:
                popup_content.append("(none)")

        popup_content.extend(['', '(Press any key to close)'])

        # Create the popup
        popup_options = {
            'title': ' Context Lists ', 'line': 0, 'col': 0,
            'minwidth': 40, 'maxwidth': 80,
            'padding': [1, 2, 1, 2], 'border': [1, 1, 1, 1],
            'borderchars': ['─', '│', '─', '│', '╭', '╮', '╯', '╰'],
            'close': 'none', 'zindex': 200,
        }
        popup_id = vim.eval(f"popup_create({json.dumps(popup_content)}, {popup_options})")
        vim.command("redraw!")
        # Wait for any key to be pressed.
        vim.eval('getchar()')

    except Exception as e:
        util.display_message(f"Error showing context lists: {e}", error=True)
    finally:
        # Ensure the popup is always closed.
        if int(popup_id) > 0:
            vim.eval(f"popup_close({popup_id})")
            vim.command("redraw!")

def confirm_context_files():
    """
    Called on BufUnload of the context files buffer.
    Shows a confirmation popup to save or discard changes to the context.
    """
    global _VIMINI_PENDING_CONTEXT_FILES
    try:
        # If the global variable was not set, something is wrong, or another
        # buffer closed. We should only act if it's populated.
        if _VIMINI_PENDING_CONTEXT_FILES is None:
            return

        pending_files = _VIMINI_PENDING_CONTEXT_FILES

        # Get the original list
        try:
            original_files = vim.eval("get(g:, 'context_files', [])")
            if not isinstance(original_files, list):
                original_files = []
        except vim.error:
            original_files = []

        # If there's no change, do nothing.
        if sorted(pending_files) == sorted(original_files):
            return

        # Build the popup content
        popup_content = ["Set new context files?", ""]
        if pending_files:
            popup_content.append("--- Files ---")
            for f in sorted(pending_files):
                popup_content.append(f"- {f}")
        else:
            popup_content.append("(Context will be empty)")

        popup_content.extend(['', '---', 'Accept changes? [y/n]'])

        popup_options = {
            'title': ' Confirm Context ', 'line': 0, 'col': 0,
            'minwidth': 40, 'maxwidth': 80,
            'padding': [1, 2, 1, 2], 'border': [1, 1, 1, 1],
            'borderchars': ['─', '│', '─', '│', '╭', '╮', '╯', '╰'],
            'close': 'none', 'zindex': 200,
        }
        popup_id = vim.eval(f"popup_create({popup_content}, {popup_options})")
        vim.command("redraw!")

        commit_confirmed = False
        try:
            answer_code = vim.eval('getchar()')
            answer_char = chr(int(answer_code))
            if answer_char.lower() == 'y':
                commit_confirmed = True
        except (vim.error, ValueError, TypeError):
            pass
        finally:
            vim.eval(f"popup_close({popup_id})")
            vim.command("redraw!")

        if commit_confirmed:
            # Use json.dumps to create a string that is a valid Vimscript list literal.
            vim.command(f"let g:context_files = {json.dumps(pending_files)}")
            util.display_message("Context files updated.", history=True)
        else:
            util.display_message("Context file changes discarded.", history=True)

    except Exception as e:
        util.display_message(f"Error confirming context files: {e}", error=True)
    finally:
        # Clean up the global variable now that the context manager is closed.
        _VIMINI_PENDING_CONTEXT_FILES = None

def _refresh_files_buffer():
    """
    Helper to re-fetch files and update the content of the 'Vimini Files' buffer.
    """
    # Find the 'Vimini Files' buffer
    vimini_files_buffer = None
    for b in vim.buffers:
        if b.valid and b.name and b.name.endswith('Vimini Files'):
            vimini_files_buffer = b
            break
    if not vimini_files_buffer:
        return

    client = util.get_client()
    if not client:
        return

    all_files = list(client.files.list())
    file_list_content = [
        "Vimini Remote Files",
        "-------------------",
        " d: delete | i: info | q: close",
        ""
    ]
    if not all_files:
        file_list_content.append("No files have been uploaded.")
    else:
        # Sorting is good for consistency
        for f in sorted(all_files, key=lambda x: x.display_name):
            file_list_content.append(f.display_name)

    # Switch to window, update buffer, switch back
    win_nr = int(vim.eval(f"bufwinnr({vimini_files_buffer.number})"))
    if win_nr > 0:
        original_win_nr = int(vim.eval("winnr()"))
        vim.command(f"{win_nr}wincmd w")
        # Save cursor position before modifying the buffer
        cursor_pos = vim.eval("getpos('.')")

        vim.command("setlocal modifiable")
        vimini_files_buffer[:] = file_list_content
        vim.command("setlocal nomodifiable")

        # Restore cursor position, adjusting if necessary
        new_line_count = len(vimini_files_buffer)
        lnum = int(cursor_pos[1])
        if lnum > new_line_count:
            lnum = new_line_count
        # Ensure line number is at least 1
        if lnum < 1:
            lnum = 1
        cursor_pos[1] = str(lnum)
        cursor_pos[0] = '0' # Use current buffer to be safe

        vim.command(f"call setpos('.', {cursor_pos})")

        if original_win_nr != win_nr:
            vim.command(f"{original_win_nr}wincmd w")

def _files_buffer_action(action):
    """
    Performs an action ('info' or 'delete') on the file under the cursor
    in the 'Vimini Files' buffer.
    """
    try:
        w = vim.current.window
        # Check if we are in the right buffer
        if not (w.valid and w.buffer.name and w.buffer.name.endswith('Vimini Files')):
            return

        line_num = w.cursor[0]
        line = w.buffer[line_num - 1].strip()

        # Ignore header/blank lines
        if not line or line.startswith("Vimini") or line.startswith("---") or "delete |" in line:
            return

        file_name = line
        client = util.get_client()
        if not client:
            return

        # Find the file object by its display_name
        util.display_message(f"Finding '{file_name}'...")
        target_file = None
        all_files = list(client.files.list())
        for f in all_files:
            if f.display_name == file_name:
                target_file = f
                break

        if not target_file:
            util.display_message(f"Error: File '{file_name}' no longer exists on server. Refreshing list.", error=True)
            _refresh_files_buffer()
            return

        util.display_message("") # Clear message

        if action == "info":
            info_content = [
                f"File Info: {target_file.display_name}",
                "---------------------------------",
                f"ID:           {target_file.name}",
                f"Display Name: {target_file.display_name}",
                f"MIME Type:    {target_file.mime_type}",
                f"Size:         {target_file.size_bytes} bytes",
                f"Created:      {target_file.create_time.isoformat()}",
                f"URI:          {target_file.uri}",
            ]
            util.new_split()
            vim.command(f'file Vimini File Info: {file_name}')
            vim.current.buffer[:] = info_content
            vim.command('setlocal buftype=nofile filetype=markdown noswapfile nomodifiable')

        elif action == "delete":
            util.display_message(f"Deleting '{file_name}'...")
            client.files.delete(name=target_file.name)
            util.display_message(f"File '{file_name}' deleted. Refreshing list...", history=True)
            _refresh_files_buffer()

    except Exception as e:
        util.display_message(f"Error during file action: {e}", error=True)

def files_command():
    """
    Opens an interactive buffer listing all remote files, with key mappings
    to manage them.
    """
    util.log_info("files_command()")
    try:
        client = util.get_client()
        if not client:
            return

        util.display_message("Fetching file list...")
        all_files = list(client.files.list())
        util.display_message("")

        file_list_content = [
            "Vimini Remote Files",
            "-------------------",
            " d: delete | i: info | q: close",
            ""
        ]
        if not all_files:
            file_list_content.append("No files have been uploaded.")
        else:
            for f in sorted(all_files, key=lambda x: x.display_name):
                file_list_content.append(f.display_name)

        util.new_split()
        vim.command('file Vimini Files')
        buf = vim.current.buffer
        buf[:] = file_list_content
        vim.command('setlocal buftype=nofile noswapfile filetype=markdown')

        # Mappings for actions
        vim.command("nnoremap <buffer> <silent> i :py3 from vimini.main import _files_buffer_action; _files_buffer_action('info')<CR>")
        vim.command("nnoremap <buffer> <silent> d :py3 from vimini.main import _files_buffer_action; _files_buffer_action('delete')<CR>")
        vim.command("nnoremap <buffer> <silent> q :q<CR>")

        vim.command('setlocal nomodifiable')

    except Exception as e:
        util.display_message(f"Error listing files: {e}", error=True)
