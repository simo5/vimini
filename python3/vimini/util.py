import vim
import os, subprocess, time, io, logging, inspect
from google import genai
from google.genai import types

# Module-level variables to store the API key, model name, and client instance.
_API_KEY = None
_MODEL = None
_GENAI_CLIENT = None # Global, lazily-initialized client.
_REPO_NAME_CACHE = None # Cache for the git repository directory name.
_LOGGER = None

def get_client():
    """
    Lazily initializes and returns the global genai.Client instance.
    """
    global _GENAI_CLIENT
    if _GENAI_CLIENT is None:
        if not _API_KEY:
            vim.command("echoerr '[Vimini] API key not set. Please run :ViminiInit'")
            return None
        try:
            vim.command("echo '[Vimini] Initializing API client...'")
            vim.command("redraw")
            _GENAI_CLIENT = genai.Client(api_key=_API_KEY)
            vim.command("echo ''") # Clear the message
        except Exception as e:
            vim.command(f"echoerr '[Vimini] Error creating API client: {e}'")
            return None
    return _GENAI_CLIENT

def new_split():
    """Creates a new split using the user's preferred method."""
    try:
        split_method = vim.eval("get(g:, 'vimini_split_method', 'vertical')")
    except (vim.error, AttributeError):
        split_method = 'vertical' # Fallback for non-vim environments

    if split_method == 'horizontal':
        vim.command('new')
    else:
        vim.command('vnew')

def get_git_repo_root():
    """
    Finds the root directory of the git repository for the current buffer.
    Returns the repository root path on success, or None on failure.

    Args:
        silent (bool): If True, suppress error messages on failure.
    """
    current_file_path = vim.current.buffer.name
    if not current_file_path:
        return None

    # Determine the root of the git repository from the current file's directory.
    start_dir = os.path.dirname(current_file_path) or '.'
    rev_parse_cmd = ['git', '-C', start_dir, 'rev-parse', '--show-toplevel']

    try:
        repo_path_result = subprocess.run(
            rev_parse_cmd,
            capture_output=True,
            text=True,
            check=False
        )
    except Exception as e:
        message = "Git command not found or failed."
        log_info(f"ERROR: {message} Exception: {e}")
        return None


    if repo_path_result.returncode != 0:
        message = "Git command not found or failed."
        log_info(f"ERROR: {message} Error: {repo_path_result.stderr}")
        return None

    return repo_path_result.stdout.strip()

def get_git_repo_name():
    """
    Fetches and caches the name of the directory of the current git repository.
    Returns "temp" if not in a git repository.
    """
    global _REPO_NAME_CACHE
    if _REPO_NAME_CACHE is not None:
        return _REPO_NAME_CACHE

    # Call get_git_repo_root silently to avoid error message recursion,
    # as this function is used within display_message itself.
    repo_path = get_git_repo_root()

    if repo_path:
        _REPO_NAME_CACHE = os.path.basename(repo_path)
    else:
        _REPO_NAME_CACHE = "temp"

    return _REPO_NAME_CACHE

def log_info(message):
    """Writes a message to the logger if it's enabled."""
    if _LOGGER:
        _LOGGER.info(str(message))

def display_message(message, error=False, history=False, filename=None, line_number=None):
    """
    Displays a message to the user in the Vim command line.
    If an error, it also writes the message to the log file if enabled.
    This function automatically determines the caller's filename and line number
    for logging purposes if they are not provided.

    Args:
        message (str): The message to display.
        error (bool): If True, display as an error message.
        history (bool): If True (and not an error), save to message history.
        filename (str, optional): The source file of the message for logging.
        line_number (int, optional): The line number of the message for logging.
    """
    if filename is None or line_number is None:
        try:
            # stack()[0] is current frame (display_message), stack()[1] is the caller's frame.
            caller_frame_record = inspect.stack()[1]
            frame = caller_frame_record[0]
            info = inspect.getframeinfo(frame)
            filename = info.filename
            line_number = info.lineno
        except (IndexError, AttributeError):
            # If we can't get caller info, just proceed without it.
            filename, line_number = None, None

    # Escape single quotes and newlines to prevent breaking the Vim command string.
    safe_message = str(message).replace("'", "\"").replace('\n', ' ').replace('\r', '')

    prefix = f"[Vimini ({get_git_repo_name()})]"
    full_message = f"{prefix} {safe_message}"

    log_context = ""
    if filename and line_number:
        log_context = f"[{os.path.basename(filename)}:{line_number}] "

    if error:
        command = "echoerr"
        # Copy the error message to the logger if it's enabled.
        log_info(f"ERROR: {log_context}{message}")
    elif history:
        command = "echom"
    else:
        # Use 'echo' for transient messages that don't need to be in history.
        command = "echo"

    try:
        vim.command(f"{command} '{full_message}'")
        # For transient messages, redraw to show them immediately without a 'Press ENTER' prompt.
        if not error and not history:
            vim.command("redraw")
    except vim.error as e:
        # Fallback in case the vim command fails. This is unlikely but good practice.
        print(f"Vimini Fallback: {full_message} (vim.command failed: {e})")
        log_info(f"ERROR: {log_context}vim.command failed for message: '{full_message}'. Details: {e}")

def create_generation_kwargs(contents, temperature=None, verbose=False, response_mime_type=None, response_schema=None):
    """
    Creates a dictionary of keyword arguments for the Gemini API's
    generate_content and generate_content_stream methods.

    Args:
        contents: The prompt/contents for the API call.
        temperature (float, optional): The generation temperature.
        verbose (bool, optional): If True, enables streaming of 'thoughts'.
        response_mime_type (str, optional): The desired MIME type for the response.
        response_schema (types.Schema, optional): The desired response schema.

    Returns:
        dict: A dictionary of keyword arguments for the API call.
    """
    generation_config = types.GenerateContentConfig()

    if temperature is not None:
        try:
            temp_float = float(temperature)
            if 0.0 <= temp_float <= 2.0:
                generation_config.temperature = temp_float
            else:
                display_message("Temperature must be between 0.0 and 2.0. Using default.", error=True)
        except (ValueError, TypeError):
            display_message(f"Invalid temperature value: {temperature}. Using default.", error=True)

    if verbose:
        generation_config.thinking_config = types.ThinkingConfig(include_thoughts=True)

    if response_mime_type:
        generation_config.response_mime_type = response_mime_type

    if response_schema:
        generation_config.response_schema = response_schema

    return {
        'model': _MODEL,
        'contents': contents,
        'config': generation_config
    }

def is_buffer_modified(buffer=None):
    """
    Checks if a given buffer (or the current one) has unsaved modifications.

    Args:
        buffer (vim.Buffer, optional): The buffer to check. Defaults to vim.current.buffer.

    Returns:
        bool: True if the buffer is modified, False otherwise.
    """
    if buffer is None:
        buffer = vim.current.buffer

    # The 'modified' option is a boolean (1 or 0) in Vim's buffer-local options.
    return bool(buffer.options['modified'])

def find_context_files():
    """
    Generate a list of files that are referenced by open buffers, ensuring each
    file path is included only once.

    If multiple buffers point to the same file path, only the first buffer
    encountered is used. Each element of the list is a tuple containing the
    file path and the corresponding vim buffer number. This function filters
    out buffers that are not backed by a file on disk.
    """
    files_to_upload = []
    seen_file_paths = set()
    for b in vim.buffers:
        # A buffer is considered file-backed if its name is not empty
        # and the file actually exists on disk.
        if b.name and os.path.exists(b.name):
            # If we haven't seen this file path before, add it.
            if b.name not in seen_file_paths:
                files_to_upload.append((b.name, b.number))
                seen_file_paths.add(b.name)
    return files_to_upload

def upload_context_files(client):
    """
    Lists previously uploaded files, then uploads the content of all relevant
    Vim buffers to use as context for the AI. It re-uploads files if they have
    been modified on disk since the last upload.
    Returns a list of active file API resources, or None on failure.
    """
    # --- 1. Determine which files to upload vs. reuse ---
    files_to_process = []
    files_requiring_upload = []
    display_message("Checking context files...")

    context_files = find_context_files()
    if not context_files:
        display_message("No file-backed buffers found to use as context.", history=True)
        return None

    # Load existing files into a map for quick lookup.
    existing_files = {}
    try:
        for f in client.files.list():
            existing_files[f.display_name] = f
    except Exception:
        # Ignore errors; if listing fails, we'll just upload everything.
        pass

    for file_path, buf_number in context_files:
        found_file = existing_files.get(file_path)

        if not found_file:
            # Not found, needs uploading.
            files_requiring_upload.append((file_path, buf_number))
            continue

        # File was found, check if it's stale.
        buffer = vim.buffers[buf_number]
        is_stale = False

        # If buffer is modified, it's stale; skip timestamp check.
        if is_buffer_modified(buffer):
            is_stale = True
        else:
            # If buffer not modified, check timestamp on disk.
            try:
                disk_mtime = os.path.getmtime(file_path)
                uploaded_time = found_file.create_time.timestamp()
                if uploaded_time < disk_mtime:
                    is_stale = True
            except (OSError, AttributeError):
                # If we can't get times, re-upload to be safe.
                is_stale = True

        if is_stale:
            files_requiring_upload.append((file_path, buf_number))
            # It's good practice to delete the old one.
            try:
                client.files.delete(name=found_file.name)
            except Exception:
                pass # Deletion is best-effort.
        else:
            # Uploaded file is recent enough, use it.
            files_to_process.append(found_file)


    # --- 2. Upload necessary files ---
    if files_requiring_upload:
        display_message(f"Uploading {len(files_requiring_upload)} context file(s)...")

    uploaded_files = []
    for file_path, buf_number in files_requiring_upload:
        buf = vim.buffers[buf_number]
        buf_content = "\n".join(buf[:])
        if not buf_content.strip():
            continue

        buf_content_bytes = buf_content.encode('utf-8')
        buf_io = io.BytesIO(buf_content_bytes)
        # Always force plain text, the GEeminiAPI is very fussy with thr type
        # and returns 400 errors on types it doesn't like
        mime_type = 'text/plain'

        try:
            uploaded_file = client.files.upload(
                file=buf_io,
                config=types.UploadFileConfig(
                    display_name=file_path,
                    mime_type=mime_type
                ),
            )
            uploaded_files.append(uploaded_file)
        except Exception as e:
            display_message(f"Error uploading {file_path}: {e}", error=True)
            return None # Fail fast on upload error

    # --- 3. Wait for all files (reused and new) to become ACTIVE ---
    pending_files = []
    for f in uploaded_files:
        # Re-check the state of reused files as well.
        if f.state.name == 'ACTIVE':
            files_to_process.append(f)
        elif f.state.name == 'PROCESSING':
            pending_files.append(f)
        else: # FAILED, etc.
             display_message(f"Reused file {f.display_name} is in an unusable state: {f.state.name}", error=True)
             return None

    start_time = time.time()
    timeout = 2.0
    while pending_files:
        if time.time() - start_time > timeout:
            display_message(f"File processing timed out after {int(timeout)}s.", error=True)
            return None
        remaining_time = timeout - (time.time() - start_time)
        display_message(f"Waiting for {len(pending_files)} files... ({remaining_time:.1f}s left)")
        time.sleep(0.1)
        still_pending = []
        for f in pending_files:
            try:
                updated_file = client.files.get(name=f.name)
                if updated_file.state.name == 'PROCESSING':
                    still_pending.append(updated_file)
                elif updated_file.state.name == 'ACTIVE':
                    files_to_process.append(updated_file)
                else: # FAILED or other terminal state
                    display_message(f"File processing failed for {updated_file.display_name}: {updated_file.state.name}", error=True)
                    return None
            except Exception as e:
                display_message(f"Error checking file status for {f.display_name}: {e}", error=True)
                return None
        pending_files = still_pending

    if not files_to_process:
        display_message("No content found in open buffers to create context.", history=True)
        return None

    return files_to_process

def set_logging(log_file=None):
    """
    Configures logging for the plugin.

    If a log_file path is provided, it sets up a logger to write to that
    file. If log_file is None, it disables logging by adding a NullHandler.
    """
    global _LOGGER

    # Use a named logger to avoid interfering with other plugins or Vim's root logger.
    logger = logging.getLogger('vimini')
    logger.setLevel(logging.INFO) # Set level regardless of handler

    # Clear existing handlers to prevent log duplication on re-initialization
    if logger.hasHandlers():
        logger.handlers.clear()

    # Stop messages from being passed to the root logger
    logger.propagate = False

    try:
        if log_file:
            # Expand user directory and variables
            log_file = os.path.expanduser(os.path.expandvars(log_file))

            # Ensure the directory for the log file exists.
            log_dir = os.path.dirname(os.path.abspath(log_file))
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)

            # Create a file handler to write to the log file.
            handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
            formatter = logging.Formatter(
                '%(asctime)s - %(levelname)s - %(message)s'
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
        else:
            # If no log file, add a NullHandler to silence logging.
            # This is better than setting _LOGGER to None, as calls to log_info()
            # will still work without error; they just won't do anything.
            logger.addHandler(logging.NullHandler())

        _LOGGER = logger

    except Exception as e:
        # If logging setup fails for any reason, fall back to a NullHandler
        # to ensure the plugin continues to function without logging.
        if logger.hasHandlers():
            logger.handlers.clear()
        logger.addHandler(logging.NullHandler())
        _LOGGER = logger
        display_message(f"Failed to initialize log file '{log_file}': {e}", error=True)