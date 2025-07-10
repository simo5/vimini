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
    """
    if not _API_KEY:
        message = "[Vimini] API key not set."
        vim.command(f"echoerr '{message}'")
        return

    try:
        # Get the content and filetype of the current buffer.
        current_buffer_content = "\n".join(vim.current.buffer[:])
        original_filetype = vim.eval('&filetype')

        # Construct the full prompt for the API.
        full_prompt = (
            f"{prompt}\n\n"
            "Based on the user's request, please generate the code. "
            "Use the following file content as context.\n\n"
            "--- FILE CONTENT ---\n"
            f"{current_buffer_content}\n"
            "--- END FILE CONTENT ---\n\n"
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
        if original_filetype:
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
