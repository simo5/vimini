import vim

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
