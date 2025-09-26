import vim
import os, subprocess, tempfile, shlex, threading, uuid, queue, textwrap
from google.genai import types
from vimini import util

# --- Autocomplete state ---
_autocomplete_lock = threading.Lock()
_current_autocomplete_job_id = None
_autocomplete_queue = queue.Queue() # Queue for thread communication
_original_cursor_hl = {} # Stores original cursor highlight settings

def initialize(api_key, model):
    """
    Initializes the plugin with the user's API keyi and model name.
    This function is called from the plugin's Vimscript entry point.
    """
    util._API_KEY = api_key
    util._MODEL = model
    util._GENAI_CLIENT = None # Reset client if key/model changes.
    if not util._API_KEY:
        message = "[Vimini] API key not found. Please set g:vimini_api_key or store it in ~/.config/gemini.token."
        vim.command(f"echoerr '{message}'")

def list_models():
    """
    Lists the available Gemini models.
    """
    try:
        client = util.get_client()
        if not client:
            return

        # Get the list of models.
        vim.command("echo '[Vimini] Fetching models...'")
        vim.command("redraw") # Force redraw to show message without 'Press ENTER'
        models = client.models.list()
        vim.command("echo ''") # Clear the message

        # Prepare the content for the new buffer.
        model_list = ["Available Models:"]
        for model in models:
            model_list.append(f"- {model.name}")

        # Display the models in a new split window.
        vim.command('vnew')
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
        vim.current.buffer[:] = model_list

    except Exception as e:
        vim.command(f"echoerr '[Vimini] Error: {e}'")

def chat(prompt):
    """
    Sends a prompt to the Gemini API and displays the response in a new buffer.
    """
    try:
        client = util.get_client()
        if not client:
            return

        # Immediately open a new split window for the chat.
        vim.command('vnew')
        vim.command('file Vimini Chat')
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')

        # Display the prompt in the new buffer.
        vim.current.buffer[:] = [f"Q: {prompt}", "---", "A:"]
        vim.command('normal! G') # Move cursor to the end to prepare for the answer
        vim.command("redraw")

        # Send the prompt and get the response.
        vim.command("echo '[Vimini] Thinking...'")
        vim.command("redraw") # Force redraw to show message without 'Press ENTER'
        response = client.models.generate_content(
            model=util._MODEL,
            contents=prompt,
        )
        vim.command("echo ''") # Clear the thinking message
        vim.current.buffer.append(response.text.split('\n'))

    except Exception as e:
        vim.command(f"echoerr '[Vimini] Error: {e}'")

def code(prompt, verbose=False):
    """
    Uploads all open files, sends them to the Gemini API with a prompt
    to generate code. Displays thoughts (if verbose), the response, and a diff
    in new buffers.
    """
    try:
        client = util.get_client()
        if not client:
            return

        # Store info from the original buffer before we do anything else.
        original_buffer = vim.current.buffer
        original_bufnr = original_buffer.number
        original_buffer_content = "\n".join(original_buffer[:])
        original_filetype = vim.eval('&filetype')

        # Upload context files using the helper function.
        uploaded_files = util.upload_context_files(client)

        # A `None` return value indicates an upload failure.
        if uploaded_files is None:
            return # The helper function has already displayed an error.

        # Construct the full prompt for the API, referencing the uploaded files.
        main_file_name = os.path.basename(original_buffer.name or f"Buffer {original_bufnr}")
        full_prompt = [
            (
                f"{prompt}\n\n"
                "Based on the user's request, please generate the code. "
                f"Your primary task is to modify the file named '{main_file_name}'. "
                "The other files have been provided for context.\n\n"
                "IMPORTANT: Only output the raw code for the modified file. Do not include any explanations, "
                "nor markdown code fences."
            ),
            *uploaded_files
        ]

        thoughts_buffer = None
        if verbose:
            # Create the Vimini Thoughts buffer before calling the model.
            vim.command('vnew')
            vim.command('file Vimini Thoughts')
            vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
            thoughts_buffer = vim.current.buffer
            thoughts_buffer[:] = ['']

        # Create the ViminiCode buffer. This becomes the active window.
        vim.command('vnew')
        vim.command('file Vimini Code')
        vim.command('setlocal buftype=nofile noswapfile')
        if original_filetype: # Apply the filetype of the original buffer to the new one
            vim.command(f'setlocal filetype={original_filetype}')
        vim.command(f"let b:vimini_source_bufnr = {original_bufnr}")
        ai_buffer = vim.current.buffer # Reference the new code buffer
        ai_buffer[:] = ['']

        # Get window numbers for faster switching during streaming.
        thoughts_win_nr = None
        if verbose:
            thoughts_win_nr = vim.eval(f"bufwinnr({thoughts_buffer.number})")
        ai_win_nr = vim.eval(f"bufwinnr({ai_buffer.number})")

        # Display a Thinking.. message so users know they have to wait
        vim.command("echo '[Vimini] Thinking...'")
        vim.command("redraw")

        # Set up the API call arguments
        stream_kwargs = {
            'model': util._MODEL,
            'contents': full_prompt
        }
        # Enable thinking only if verbose is requested
        if verbose:
            stream_kwargs['config'] = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(
                    include_thoughts=True
                )
            )

        # Use generate_content_stream()
        response_stream = client.models.generate_content_stream(**stream_kwargs)
        has_stripped_opening_fence = False

        for chunk in response_stream:
            if not chunk.candidates:
                continue
            if not chunk.candidates[0].content:
                continue
            if not chunk.candidates[0].content.parts:
                continue
            for part in chunk.candidates[0].content.parts:
                if not part.text:
                    continue

                is_thought = hasattr(part, 'thought') and part.thought
                target_buffer = thoughts_buffer if is_thought else ai_buffer

                # Switch to the window displaying the buffer being updated.
                target_win_nr = thoughts_win_nr if is_thought else ai_win_nr
                if int(target_win_nr) > 0:
                    vim.command(f"{target_win_nr}wincmd w")

                # Split incoming text by newlines to handle chunks that span multiple lines
                new_lines = part.text.split('\n')

                # Append the first part of the new text to the current last line in the buffer
                target_buffer[-1] += new_lines[0]

                # If the chunk contained one or more newlines, add the rest as new lines
                if len(new_lines) > 1:
                    target_buffer.append(new_lines[1:])

                if not is_thought:
                    # Check for and strip the opening code fence as soon as it appears.
                    if not has_stripped_opening_fence and ai_buffer and ai_buffer[0].lstrip().startswith('```'):
                        ai_buffer[:] = ai_buffer[1:] # reset buffer with all content but the first line
                        has_stripped_opening_fence = True

                # Move cursor to the end and scroll view to keep the last line visible.
                vim.command('normal! Gz-')
                vim.command("echo '[Vimini] Thinking...'")
                vim.command('redraw')

        vim.command("echo ''") # Clear the thinking message

        # After the loop, remove the closing fence if it's the last line in the code buffer
        if ai_buffer and ai_buffer[-1].strip() == '```':
            ai_buffer[:] = ai_buffer[:-1]

        # Reconstruct the final, cleaned code string for the diff logic that follows
        ai_generated_code = "\n".join(list(ai_buffer))

        # Force a redraw to show the final state after any last-line stripping
        vim.command("echo '[Vimini] Thinking...'")
        vim.command("redraw!")

        # --- Generate and display the diff ---
        with tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.orig', encoding='utf-8') as f_orig, \
             tempfile.NamedTemporaryFile(mode='w+', delete=False, suffix='.ai', encoding='utf-8') as f_ai:
            f_orig.write(original_buffer_content)
            f_ai.write(ai_generated_code)
            orig_filepath = f_orig.name
            ai_filepath = f_ai.name

        try:
            cmd = ['diff', '-u', orig_filepath, ai_filepath]
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
            if result.returncode > 1:
                error_message = result.stderr.strip().replace("'", "''")
                vim.command(f"echoerr '[Vimini] Could not generate diff: {error_message}'")
                return
            diff_output = result.stdout
        finally:
            os.remove(orig_filepath)
            os.remove(ai_filepath)

        if not diff_output.strip():
            vim.command("echom '[Vimini] AI content is identical to the original.'")
            return

        # Open a new split window for the diff.
        vim.command('vnew')
        vim.command('file Vimini Diff')
        vim.command('setlocal buftype=nofile filetype=diff noswapfile')
        vim.current.buffer[:] = diff_output.split('\n')
        vim.command('normal! 1G')

    except Exception as e:
        vim.command(f"echoerr '[Vimini] Error: {e}'")

def review(prompt, git_objects=None, verbose=False):
    """
    Sends content to the Gemini API for a code review.
    If git_objects are provided, it reviews the output of `git show <objects>`.
    Otherwise, it reviews the content of the current buffer.
    The review is displayed in a new buffer, streaming thoughts if verbose.
    """
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
                    vim.command("echoerr '[Vimini] Security error: Git options (like flags starting with ''-'') are not allowed.'")
                    return

            cmd = ['git', '-C', repo_path, 'show'] + objects_to_show

            vim.command(f"echo '[Vimini] Running git show {git_objects}... '")
            vim.command("redraw")
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if result.returncode != 0:
                error_message = (result.stderr or "git show failed.").strip().replace("'", "''")
                vim.command(f"echoerr '[Vimini] Git error: {error_message}'")
                return
            vim.command("echo ''") # Clear message

            review_content = result.stdout

            # As requested, open a new buffer with the git show output, which becomes the context
            vim.command('vnew')
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
            vim.command("echom '[Vimini] Nothing to review.'")
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
            vim.command('vnew')
            vim.command('file Vimini Thoughts')
            vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
            thoughts_buffer = vim.current.buffer
            thoughts_buffer[:] = ['']

        # Create the Vimini Review buffer. This becomes the active window.
        vim.command('vnew')
        vim.command('file Vimini Review')
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
        review_buffer = vim.current.buffer # Reference the new review buffer
        review_buffer[:] = ['']

        # Get window numbers for faster switching during streaming.
        thoughts_win_nr = None
        if verbose:
            thoughts_win_nr = vim.eval(f"bufwinnr({thoughts_buffer.number})")
        review_win_nr = vim.eval(f"bufwinnr({review_buffer.number})")

        # Display a Thinking.. message so users know they have to wait
        vim.command("echo '[Vimini] Thinking...'")
        vim.command("redraw")

        # Set up the API call arguments
        stream_kwargs = {
            'model': util._MODEL,
            'contents': full_prompt
        }
        # Enable thinking only if verbose is requested
        if verbose:
            stream_kwargs['config'] = types.GenerateContentConfig(
                thinking_config=types.ThinkingConfig(
                    include_thoughts=True
                )
            )

        # Use generate_content_stream()
        response_stream = client.models.generate_content_stream(**stream_kwargs)

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
                vim.command("echo '[Vimini] Thinking...'")
                vim.command('redraw')

        vim.command("echo ''") # Clear the thinking message

    except FileNotFoundError:
        vim.command("echoerr '[Vimini] Error: `git` command not found. Is it in your PATH?'")
    except Exception as e:
        vim.command(f"echoerr '[Vimini] Error: {e}'")

def show_diff():
    """
    Shows the current git modifications in a new buffer.
    """
    try:
        repo_path = util.get_git_repo_root()
        if not repo_path:
            return # Error message handled by helper

        # Command to get the diff.
        # -C ensures git runs in the correct directory.
        cmd = ['git', '-C', repo_path, 'diff', '--color=never']

        # Execute the command.
        vim.command("echo '[Vimini] Running git diff...'")
        vim.command("redraw")
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        vim.command("echo ''") # Clear message

        # Handle git errors (e.g., not a git repository).
        if result.returncode != 0 and not result.stdout.strip():
            # Escape single quotes for Vim's echoerr
            error_message = result.stderr.strip().replace("'", "''")
            vim.command(f"echoerr '[Vimini] Git error: {error_message}'")
            return

        # Handle case with no modifications.
        if not result.stdout.strip():
            vim.command("echom '[Vimini] No modifications found.'")
            return

        # Display the diff in a new split window.
        vim.command('vnew')
        vim.command('file Git Diff')
        # Setting filetype to 'diff' helps with syntax highlighting
        vim.command('setlocal buftype=nofile filetype=diff noswapfile')

        # The output from git contains ANSI escape codes for color.
        # We place this raw output into the buffer.
        vim.current.buffer[:] = result.stdout.split('\n')

    except FileNotFoundError:
        vim.command("echoerr '[Vimini] Error: `git` command not found. Is it in your PATH?'")
    except Exception as e:
        vim.command(f"echoerr '[Vimini] Error: {e}'")

def commit(author=None):
    """
    Generates a detailed commit message (subject and body) using the Gemini
    API based on all current changes. It stages everything, shows the generated
    message in a popup for review, and then commits, optionally with a
    'Co-authored-by' trailer.
    """
    try:
        repo_path = util.get_git_repo_root()
        if not repo_path:
            return # Error handled by helper

        # Stage all changes to get a complete diff for the commit message.
        vim.command("echo '[Vimini] Staging all changes... (git add .)'")
        vim.command("redraw")
        add_cmd = ['git', '-C', repo_path, 'add', '.']
        add_result = subprocess.run(add_cmd, capture_output=True, text=True, check=False)

        if add_result.returncode != 0:
            error_message = (add_result.stderr or add_result.stdout).strip().replace("'", "''")
            vim.command(f"echoerr '[Vimini] Git add failed: {error_message}'")
            return
        vim.command("echo ''")

        # Get the diff of what was just staged.
        staged_diff_cmd = ['git', '-C', repo_path, 'diff', '--staged']
        staged_diff_result = subprocess.run(staged_diff_cmd, capture_output=True, text=True, check=False)

        if staged_diff_result.returncode != 0:
            error_message = staged_diff_result.stderr.strip().replace("'", "''")
            vim.command(f"echoerr '[Vimini] Git error getting staged diff: {error_message}'")
            return

        diff_to_process = staged_diff_result.stdout.strip()

        if not diff_to_process:
            vim.command("echom '[Vimini] No changes to commit.'")
            return

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

        vim.command("echo '[Vimini] Generating commit message... (this may take a moment)'")
        vim.command("redraw")

        client = util.get_client()
        if not client:
            vim.command("echom '[Vimini] Commit cancelled (client init failed). Reverting `git add`.'")
            reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
            subprocess.run(reset_cmd, check=False)
            return

        response = client.models.generate_content(
            model=util._MODEL,
            contents=prompt,
        )
        vim.command("echo ''")

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
            vim.command("echoerr '[Vimini] Failed to generate a commit message. Reverting `git add`.'")
            reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
            subprocess.run(reset_cmd, check=False)
            return

        # Show the generated message in a popup for review and confirmation.
        popup_content = [f"Subject: {subject}", ""]
        if body:
            popup_content.extend(body.split('\n'))
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
            vim.command("echom '[Vimini] Commit cancelled. Reverting `git add`.'")
            reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
            subprocess.run(reset_cmd, check=False)
            return

        # Construct the commit command with subject, body, and sign-off.
        commit_cmd = ['git', '-C', repo_path, 'commit', '-s', '-m', subject]
        if body:
            commit_cmd.extend(['-m', body])
        if author:
            commit_cmd.extend(['-m', '', '-m', author]) # Blank line before trailer.

        display_message = subject.replace("'", "''")
        vim.command(f"echom '[Vimini] Committing with subject: {display_message}'")
        vim.command("redraw")

        commit_result = subprocess.run(commit_cmd, capture_output=True, text=True, check=False)

        if commit_result.returncode == 0:
            success_message = commit_result.stdout.strip().replace("'", "''").split('\n')[0]
            vim.command(f"echom '[Vimini] Commit successful: {success_message}'")
        else:
            error_message = (commit_result.stderr or commit_result.stdout).strip().replace("'", "''")
            vim.command(f"echoerr '[Vimini] Git commit failed: {error_message}'")

    except FileNotFoundError:
        vim.command("echoerr '[Vimini] Error: `git` command not found. Is it in your PATH?'")
    except Exception as e:
        vim.command(f"echoerr '[Vimini] Error: {e}'")

def apply_code():
    """
    Finds the 'Vimini Code' buffer, copies its contents over the original
    buffer, and closes the temporary Vimini Code and Vimini Diff buffers.
    """
    # Locate the source 'Vimini Code' buffer, which contains the AI-generated code.
    ai_buffer = None
    diff_buffer = None
    thoughts_buffer = None
    for buf in vim.buffers:
        if buf.name and buf.name.endswith('Vimini Code'):
            ai_buffer = buf
        elif buf.name and buf.name.endswith('Vimini Diff'):
            diff_buffer = buf
        elif buf.name and buf.name.endswith('Vimini Thoughts'):
            thoughts_buffer = buf

    if not ai_buffer:
        vim.command("echoerr '[Vimini] `Vimini Code` buffer not found. Was :ViminiCode run?'")
        return

    # To find the original buffer, retrieve the buffer number that `code()` saved
    # in the 'Vimini Code' buffer's local variables.
    original_bufnr = int(vim.eval(f"getbufvar({ai_buffer.number}, 'vimini_source_bufnr', -1)"))
    if original_bufnr == -1:
        vim.command("echoerr '[Vimini] Could not find the original buffer. The link may have been lost.'")
        return

    # Find the original buffer object from its number and ensure it's still valid.
    original_buffer = next((b for b in vim.buffers if b.number == original_bufnr), None)
    if not original_buffer or not original_buffer.valid:
        vim.command(f"echoerr '[Vimini] The original buffer ({original_bufnr}) no longer exists.'")
        return

    # Get content and name before buffers are modified or deleted.
    ai_content = ai_buffer[:]
    original_buffer_name = original_buffer.name or '[No Name]'

    # Perform the overwrite. This automatically marks the buffer as modified.
    original_buffer[:] = ai_content

    # Switch window focus to the modified buffer.
    target_win_nr = vim.eval(f"bufwinnr({original_buffer.number})")
    if int(target_win_nr) > 0:
        vim.command(f"{target_win_nr}wincmd w")
    else:
        # If no window is showing it, open it in the current window.
        vim.command(f"buffer {original_buffer.number}")

    # Clean up temporary buffers. Use '!' to discard any unsaved changes.
    vim.command(f"bdelete! {ai_buffer.number}")
    if diff_buffer and diff_buffer.number in [b.number for b in vim.buffers]:
        vim.command(f"bdelete! {diff_buffer.number}")
    if thoughts_buffer and thoughts_buffer.number in [b.number for b in vim.buffers]:
        vim.command(f"bdelete! {thoughts_buffer.number}")

    vim.command(f"echom '[Vimini] Applied changes to `{original_buffer_name}`.'")

def append_code():
    """
    Finds the 'Vimini Code' buffer, appends its contents to the original
    buffer, and closes the temporary Vimini Code and Vimini Diff buffers.
    """
    # Locate the source 'Vimini Code' buffer, which contains the AI-generated code.
    ai_buffer = None
    diff_buffer = None
    thoughts_buffer = None
    for buf in vim.buffers:
        if buf.name and buf.name.endswith('Vimini Code'):
            ai_buffer = buf
        elif buf.name and buf.name.endswith('Vimini Diff'):
            diff_buffer = buf
        elif buf.name and buf.name.endswith('Vimini Thoughts'):
            thoughts_buffer = buf

    if not ai_buffer:
        vim.command("echoerr '[Vimini] `Vimini Code` buffer not found. Was :ViminiCode run?'")
        return

    # To find the original buffer, retrieve the buffer number that `code()` saved
    # in the 'Vimini Code' buffer's local variables.
    original_bufnr = int(vim.eval(f"getbufvar({ai_buffer.number}, 'vimini_source_bufnr', -1)"))
    if original_bufnr == -1:
        vim.command("echoerr '[Vimini] Could not find the original buffer. The link may have been lost.'")
        return

    # Find the original buffer object from its number and ensure it's still valid.
    original_buffer = next((b for b in vim.buffers if b.number == original_bufnr), None)
    if not original_buffer or not original_buffer.valid:
        vim.command(f"echoerr '[Vimini] The original buffer ({original_bufnr}) no longer exists.'")
        return

    # Get content and name before buffers are modified or deleted.
    ai_content = ai_buffer[:]
    original_buffer_name = original_buffer.name or '[No Name]'

    # Perform the append. This automatically marks the buffer as modified.
    # Add a newline at the end if the buffer is not empty and doesn't end with one.
    if len(original_buffer) > 0 and original_buffer[-1]:
        original_buffer.append('')
    original_buffer.append(ai_content)

    # Switch window focus to the modified buffer.
    target_win_nr = vim.eval(f"bufwinnr({original_buffer.number})")
    if int(target_win_nr) > 0:
        vim.command(f"{target_win_nr}wincmd w")
    else:
        # If no window is showing it, open it in the current window.
        vim.command(f"buffer {original_buffer.number}")

    # Clean up temporary buffers. Use '!' to discard any unsaved changes.
    vim.command(f"bdelete! {ai_buffer.number}")
    if diff_buffer and diff_buffer.number in [b.number for b in vim.buffers]:
        vim.command(f"bdelete! {diff_buffer.number}")
    if thoughts_buffer and thoughts_buffer.number in [b.number for b in vim.buffers]:
        vim.command(f"bdelete! {thoughts_buffer.number}")

    vim.command(f"echom '[Vimini] Appended code to `{original_buffer_name}`.'")

def cancel_autocomplete():
    """
    Signals that any ongoing autocomplete job should be cancelled.
    This is called from Vimscript when the user types or leaves insert mode.
    """
    global _current_autocomplete_job_id
    with _autocomplete_lock:
        _current_autocomplete_job_id = None
        # Clear any stale results from a previously cancelled job.
        while not _autocomplete_queue.empty():
            try:
                _autocomplete_queue.get_nowait()
            except queue.Empty:
                break

def _show_autocomplete_popup(suggestion):
    """
    Shows the popup with the autocomplete suggestion.
    This function must be called from Vim's main thread.
    """
    try:
        popup_options = {
            'line': 'cursor-1', 'col': 'cursor', 'close': 'none',
            'border': [0, 0, 0, 0], 'padding': [0, 1, 0, 1],
            'highlight': 'Pmenu', 'zindex': 200, 'moved': 'any',
        }

        popup_id = vim.eval(f"popup_create('{suggestion}', {popup_options})")
        if popup_id == 0:
            return
        vim.command("redraw!")

        # Block for a single character to decide whether to accept
        try:
            key_code = vim.eval("getcharstr(-1)")
            if key_code == "\t":  # Tab accepts the suggestion.
                vim.command(f"call feedkeys('{suggestion}', 'n')")
            else:
                vim.command(f"call feedkeys('{key_code}', 'n')")
        except vim.error: # Also catches Vim:Interrupt from Ctrl-C.
            pass
        finally:
            # Ensure the popup is always closed, no matter what key was pressed.
            vim.eval(f"popup_close({popup_id})")
            # Redraw to clear any screen artifacts from the popup.
            vim.command("redraw!")

    except Exception as e:
        error_message = str(e).replace("'", "''")
        vim.command(f"echoerr '[Vimini] Autocomplete popup Error: {error_message}'")

def process_autocomplete_queue():
    """
    Process one item from the autocomplete queue.
    This function is designed to be called repeatedly from a Vim timer.
    """
    try:
        task_type, data = _autocomplete_queue.get_nowait()
        if task_type == 'popup':
            # The popup function handles restoring the cursor itself.
            _show_autocomplete_popup(data)
        elif task_type == 'error':
            vim.command(f"echom '[Vimini] Autocomplete Error: {data}'")
    except queue.Empty:
        pass # Queue is empty, nothing to do.
    except Exception as e:
        error_message = str(e).replace("'", "''")
        vim.command(f"echom '[Vimini] Queue processing error: {error_message}'")

def _autocomplete_worker(job_id, buffer_content, cursor_pos, verbose):
    """
    The background worker for autocomplete. Makes the API call and puts the
    result into a queue for the main thread to process.
    """
    try:
        row, col = cursor_pos # `row` is 1-based.

        with _autocomplete_lock:
            if _current_autocomplete_job_id != job_id:
                return

        client = util.get_client()
        if not client:
            return

        start_line_index = max(0, row - 20) # Use more context
        context_lines = buffer_content[start_line_index:row]

        if not context_lines:
            return

        current_line_content = context_lines[-1]
        context_lines[-1] = current_line_content[:col] + "<CURSOR>" + current_line_content[col:]
        context_text = "\n".join(context_lines)

        prompt = (
            "You are an expert coding assistant. Based on the following code snippet, "
            "provide a single-line code completion for the position marked by `<CURSOR>`.\n"
            "IMPORTANT: Return only the code to be inserted. Do not include the original line, "
            "any explanations, quotes, or markdown formatting.\n\n"
            "--- CODE ---\n"
            f"{context_text}\n"
            "--- END CODE ---"
        )

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )

        with _autocomplete_lock:
            if _current_autocomplete_job_id != job_id:
                return

        suggestion = response.text.strip().split('\n')[0]
        if not suggestion:
            return

        _autocomplete_queue.put(('popup', suggestion))

    except Exception as e:
        error_message = str(e).replace("'", "''")
        _autocomplete_queue.put(('error', error_message))

def autocomplete(verbose=False):
    """
    Gets context from the current buffer and starts a background thread to
    fetch a single-line completion from the Gemini API.
    """
    global _current_autocomplete_job_id

    # Prevent multiple autocomplete jobs from running and overwriting the cursor color.
    if _original_cursor_hl:
        return

    if vim.eval('mode()') != 'i':
        return

    job_id = uuid.uuid4()
    buffer_content = list(vim.current.buffer)
    cursor_pos = vim.current.window.cursor

    with _autocomplete_lock:
        _current_autocomplete_job_id = job_id

    if verbose:
        vim.command("echo '[Vimini] Autocompleting...'")

    thread = threading.Thread(
        target=_autocomplete_worker,
        args=(job_id, buffer_content, cursor_pos, verbose),
        daemon=True
    )
    thread.start()
