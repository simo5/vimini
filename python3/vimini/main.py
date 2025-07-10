import vim
import os, subprocess
from google import genai

# Module-level variables to store the API key and model name for later use.
_API_KEY = None
_MODEL = None

def initialize(api_key, model):
    """
    Initializes the plugin with the user's API keyi and model name.
    This function is called from the plugin's Vimscript entry point.
    """
    global _API_KEY, _MODEL
    _API_KEY = api_key
    _MODEL = model
    if not _API_KEY:
        message = "[Vimini] API key not found. Please set g:vimini_api_key or store it in ~/.config/gemini_token."
        vim.command(f"echoerr '{message}'")

def list_models():
    """
    Lists the available Gemini models.
    """
    if not _API_KEY:
        message = "[Vimini] API key not set."
        vim.command(f"echoerr '{message}'")
        return

    try:
        # Configure the genai library with the API key.
        client = genai.Client(api_key=_API_KEY)

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
    if not _API_KEY:
        message = "[Vimini] API key not set."
        vim.command(f"echoerr '{message}'")
        return

    try:
        # Configure the genai library with the API key.
        client = genai.Client(api_key=_API_KEY)

        # Immediately open a new split window for the chat.
        vim.command('vnew')
        vim.command('file Vimini Chat')
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')

        # Display the prompt in the new buffer.
        vim.current.buffer[:] = [f"Q: {prompt}", "---", "A:"]
        vim.command('normal! G') # Move cursor to the end to prepare for the answer

        # Send the prompt and get the response.
        vim.command("echo '[Vimini] Thinking...'")
        vim.command("redraw") # Force redraw to show message without 'Press ENTER'
        response = client.models.generate_content(
            model=_MODEL,
            contents=prompt,
        )
        vim.command("echo ''") # Clear the thinking message
        vim.current.buffer.append(response.text.split('\n'))

    except Exception as e:
        vim.command(f"echoerr '[Vimini] Error: {e}'")

def code(prompt):
    """
    Sends the current buffer content along with a prompt to the Gemini API
    to generate code, and displays the response in a new buffer.
    Modified to include all open buffers as context.
    """
    if not _API_KEY:
        message = "[Vimini] API key not set."
        vim.command(f"echoerr '{message}'")
        return

    try:
        # Get the filetype of the buffer from which the 'code' function was called.
        # This will be used to set the filetype of the new generated code buffer.
        original_filetype = vim.eval('&filetype')

        context_parts = []
        # Iterate through all open buffers in Vim
        for buf in vim.buffers:
            # Get properties for the current buffer in the loop
            buf_name = buf.name if buf.name else '' # Use empty string for unnamed buffers
            buf_content = "\n".join(buf[:]) # Get all lines from the buffer
            buf_number = buf.number # Get the Vim buffer number

            # Retrieve buffer-specific options using vim.eval for accuracy
            buf_filetype = vim.eval(f"getbufvar({buf_number}, '&filetype')") or "text"
            buf_buftype = vim.eval(f"getbufvar({buf_number}, '&buftype')")

            # Filter out irrelevant buffers:
            # 1. Skip if the buffer content is empty and it's an unnamed buffer (e.g., scratchpad).
            # 2. Skip common non-file/temporary buffer types ('nofile', 'terminal', 'help', 'nowrite')
            #    UNLESS it's the current active buffer. The current buffer should always be considered
            #    primary context, regardless of its buftype, as the user initiated the action from it.
            if not buf_content.strip() and not buf_name:
                continue

            if buf_buftype in ['nofile', 'terminal', 'help', 'nowrite'] and buf != vim.current.buffer:
                continue

            # Determine if this buffer is the currently active one
            is_current_buffer = (buf == vim.current.buffer)

            # Format the header for clarity in the prompt context
            display_name = buf_name if buf_name else f"[Buffer {buf_number}]"
            header = f"--- {'CURRENT FILE' if is_current_buffer else 'FILE'} '{display_name}' ({buf_filetype}) ---"

            context_parts.append(header)
            context_parts.append(buf_content)
            context_parts.append("--- END FILE ---")
            context_parts.append("") # Add a blank line for separation between files

        # Combine all collected buffer contents into a single string for the prompt
        context_str = "\n".join(context_parts).strip()

        # Construct the full prompt for the API.
        full_prompt = (
            f"{prompt}\n\n"
            "Based on the user's request, please generate the code. "
            "Use the following open buffers as context. The current active buffer is explicitly marked as 'CURRENT FILE'.\n\n"
            f"{context_str}\n\n"
            "IMPORTANT: Only output the raw code. Do not include any explanations, "
            "nor markdown code fences"
        )

        # Configure the genai library with the API key.
        client = genai.Client(api_key=_API_KEY)

        # Send the prompt and get the response.
        vim.command("echo '[Vimini] Thinking...'")
        vim.command("redraw") # Force redraw to show message without 'Press ENTER'
        response = client.models.generate_content(
            model=_MODEL,
            contents=full_prompt,
        )
        vim.command("echo ''") # Clear the thinking message

        # Open a new split window for the generated code.
        vim.command('vnew')
        vim.command('file Vimini Code')
        vim.command('setlocal buftype=nofile noswapfile')
        if original_filetype: # Apply the filetype of the original buffer to the new one
            vim.command(f'setlocal filetype={original_filetype}')

        # Display the response in the new buffer.
        vim.current.buffer[:] = response.text.split('\n')

    except Exception as e:
        vim.command(f"echoerr '[Vimini] Error: {e}'")

def review(prompt):
    """
    Sends the current buffer content to the Gemini API for a code review,
    and displays the review in a new buffer.
    """
    if not _API_KEY:
        message = "[Vimini] API key not set."
        vim.command(f"echoerr '{message}'")
        return

    try:
        # Get the content of the current buffer.
        current_buffer_content = "\n".join(vim.current.buffer[:])
        original_filetype = vim.eval('&filetype') or 'text' # Default to text if no filetype

        # Construct the full prompt for the API.
        full_prompt = (
            f"Please review the following {original_filetype} code for potential issues, "
            "improvements, best practices, and any possible bugs. "
            "Provide a concise summary and actionable suggestions.\n\n"
            "--- FILE CONTENT ---\n"
            f"{current_buffer_content}\n"
            "--- END FILE CONTENT ---"
            f"{prompt}\n"
        )

        # Configure the genai library with the API key.
        client = genai.Client(api_key=_API_KEY)

        # Send the prompt and get the response.
        vim.command("echo '[Vimini] Generating review...'")
        vim.command("redraw") # Force redraw to show message without 'Press ENTER'
        response = client.models.generate_content(
            model=_MODEL,
            contents=full_prompt,
        )
        vim.command("echo ''") # Clear the message

        # Open a new split window for the generated review.
        vim.command('vnew')
        vim.command('file Vimini Review')
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')

        # Display the response in the new buffer.
        vim.current.buffer[:] = response.text.split('\n')

    except Exception as e:
        vim.command(f"echoerr '[Vimini] Error: {e}'")

def show_diff():
    """
    Shows the current git modifications in a new buffer.
    """
    try:
        current_file_path = vim.current.buffer.name
        if not current_file_path:
            vim.command("echoerr '[Vimini] Cannot run git diff on an unnamed buffer.'")
            return

        # Use the directory of the current file as the git repository root.
        repo_path = os.path.dirname(current_file_path)

        # Command to get the colorized diff.
        # -C ensures git runs in the correct directory.
        # --color=always forces color output even when piping.
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

def commit():
    """
    Generates a detailed commit message (subject and body) using the Gemini
    API based on all current changes. It stages everything, shows the generated
    message in a popup for review, and then commits with a 'Co-authored-by' trailer.
    """
    if not _API_KEY:
        message = "[Vimini] API key not set."
        vim.command(f"echoerr '{message}'")
        return

    try:
        current_file_path = vim.current.buffer.name
        if not current_file_path:
            vim.command("echoerr '[Vimini] Cannot determine git repository from an unnamed buffer.'")
            return

        # Determine the root of the git repository.
        start_dir = os.path.dirname(current_file_path) or '.'
        rev_parse_cmd = ['git', '-C', start_dir, 'rev-parse', '--show-toplevel']
        repo_path_result = subprocess.run(rev_parse_cmd, capture_output=True, text=True, check=False)

        if repo_path_result.returncode != 0:
            error_message = (repo_path_result.stderr or "Not a git repository.").strip().replace("'", "''")
            vim.command(f"echoerr '[Vimini] Git error: {error_message}'")
            return

        repo_path = repo_path_result.stdout.strip()

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
        client = genai.Client(api_key=_API_KEY)
        response = client.models.generate_content(
            model=_MODEL,
            contents=prompt,
        )
        vim.command("echo ''")

        # Parse the response into subject and body.
        response_text = response.text.strip()
        if '---' in response_text:
            parts = response_text.split('---', 1)
            subject = parts[0].strip()
            body = parts[1].strip() if len(parts) > 1 else ""
        else:  # Fallback if model doesn't follow instructions.
            lines = response_text.split('\n')
            subject = lines[0].strip()
            body = '\n'.join(lines[1:]).strip()

        if not subject:
            vim.command("echoerr '[Vimini] Failed to generate a commit message. Reverting `git add`.'")
            reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
            subprocess.run(reset_cmd, check=False)
            return

        # Show the generated message in a popup for review.
        popup_content = [f"Subject: {subject}", ""]
        if body:
            popup_content.extend(body.split('\n'))

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
        # Use vim.eval to call Vim's popup_create function. This is more
        # compatible across different Vim/Neovim versions than vim.fn.
        # A Python List of strings is also compatible with a Vimscript List.
        popup_id = vim.eval(f"popup_create({popup_content}, {popup_options})")

        vim.command("echom '[Vimini] Press any key to commit, or Ctrl-C to cancel.'")
        vim.command("redraw")

        commit_cancelled = False
        try:
            # We don't need the return value, just wait for a key press.
            # Hitting Ctrl-C will interrupt this call and raise a vim.error.
            vim.eval('getchar()')
        except vim.error:  # Catch Vim:Interrupt from Ctrl-C.
            commit_cancelled = True

        # Close the popup regardless of user input. The ID from vim.eval is a string.
        vim.eval(f"popup_close({popup_id})")
        vim.command("echo ''") # Clear confirmation message.

        # If user cancelled, revert the staging and exit.
        if commit_cancelled:
            vim.command("echom '[Vimini] Commit cancelled. Reverting `git add`.'")
            reset_cmd = ['git', '-C', repo_path, 'reset', 'HEAD', '--']
            subprocess.run(reset_cmd, check=False)
            return

        # Construct the commit command with subject, body, sign-off, and trailer.
        gemini_trailer = "Co-authored-by: Gemini <vimini@google.com>"
        commit_cmd = ['git', '-C', repo_path, 'commit', '-s', '-m', subject]
        if body:
            commit_cmd.extend(['-m', body])
        commit_cmd.extend(['-m', '', '-m', gemini_trailer]) # Blank line before trailer.

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
