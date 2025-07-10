import vim
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
