import vim
from google import genai

# Module-level variable to store the API key for later use.
_API_KEY = None

def initialize(api_key):
    """
    Initializes the plugin with the user's API key.
    This function is called from the plugin's Vimscript entry point.
    """
    global _API_KEY
    _API_KEY = api_key
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
