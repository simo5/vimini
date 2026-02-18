import vim
import os, subprocess, time, io, logging, inspect
import threading
import queue
from google import genai
from google.genai import types

# Module-level variables to store the API key, model name, and client instance.
_API_KEY = None
_MODEL = None
_GENAI_CLIENT = None # Global, lazily-initialized client.
_REPO_NAME_CACHE = None # Cache for the git repository directory name.
_REPO_ROOT_CACHE = None # Cache for the git repository root path.
_LOGGER = None

_STATUS_BUFFER_NAME = "Vimini Status"

# --- Async Job Management ---
_JOB_QUEUE = queue.Queue()
_JOB_COUNTER = 0
_ACTIVE_JOBS = {}
_JOB_NAMES = {}
_JOB_CLIENTS = {}

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
    Also caches the full path to the repo root for reuse by other functions.
    Returns "temp" if not in a git repository.
    """
    global _REPO_NAME_CACHE, _REPO_ROOT_CACHE
    if _REPO_NAME_CACHE is not None:
        return _REPO_NAME_CACHE

    # Call get_git_repo_root silently to avoid error message recursion,
    # as this function is used within display_message itself.
    repo_path = get_git_repo_root()

    if repo_path:
        _REPO_ROOT_CACHE = repo_path
        _REPO_NAME_CACHE = os.path.basename(repo_path)
    else:
        _REPO_ROOT_CACHE = None
        _REPO_NAME_CACHE = "temp"

    return _REPO_NAME_CACHE

def get_relative_path(file_path):
    """
    Computes a path for a file relative to its git repository root,
    or to the user's home directory as a fallback.
    Prepends the capitalized git repo name or 'HOME' to the path.
    """
    if not file_path:
        return ""

    abs_path = os.path.abspath(file_path)

    # Prime the repo root cache. This assumes we are operating within a single
    # project context, so we use the repo of the current buffer for all files.
    repo_name = get_git_repo_name() # This also populates _REPO_ROOT_CACHE
    git_root = _REPO_ROOT_CACHE

    if git_root and abs_path.startswith(git_root):
        relative_path = os.path.relpath(abs_path, git_root)
        return f"{repo_name.upper()}:{relative_path}"

    home_dir = os.path.expanduser('~')
    # Check if the path is inside the home directory.
    if abs_path.startswith(home_dir):
        try:
            relative_path = os.path.relpath(abs_path, home_dir)
            return f"HOME:{relative_path}"
        except ValueError:
            # This can happen on Windows if home_dir and abs_path are on different drives,
            # even with startswith check if symlinks are involved. Fallback is safe.
            pass

    # Fallback for files not in git repo or home, or on different drives on Windows.
    return os.path.basename(abs_path)

def get_absolute_path_from_api_path(api_path):
    """
    Inverse of get_relative_path. Converts a path from the API format
    (e.g., 'REPO:src/main.py') back to an absolute path on the local filesystem.
    """
    if not api_path:
        return ""

    project_root = get_git_repo_root() or vim.eval('getcwd()')

    if ':' not in api_path:
        # Fallback for paths without a prefix: assume relative to project root.
        return os.path.join(project_root, api_path)

    prefix, relative_path = api_path.split(':', 1)

    # This ensures the caches are populated
    repo_name = get_git_repo_name()
    git_root = _REPO_ROOT_CACHE # Populated by get_git_repo_name()

    if git_root and prefix == repo_name.upper():
        return os.path.join(git_root, relative_path)

    if prefix == 'HOME':
        home_dir = os.path.expanduser('~')
        return os.path.join(home_dir, relative_path)

    # If the prefix doesn't match, it might be a new file within the project.
    # Treat the whole string as a path relative to the project root. This
    # handles cases where the model returns a simple relative path for a new file.
    return os.path.join(project_root, api_path)

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
    safe_message = str(message).replace("'", '"').replace('\n', ' ').replace('\r', '')

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

def reserve_next_job_id(job_name="Unknown", client=None):
    """
    Reserves and returns the next available job ID.
    """
    global _JOB_COUNTER, _JOB_NAMES, _JOB_CLIENTS
    _JOB_COUNTER += 1
    _JOB_NAMES[_JOB_COUNTER] = job_name
    if client:
        _JOB_CLIENTS[_JOB_COUNTER] = client
    return _JOB_COUNTER

def start_async_job(client, kwargs, callbacks, job_id=None, job_name="Unknown", target_func=None):
    """
    Starts an asynchronous job to call the Gemini API.

    Args:
        client: The genai.Client instance.
        kwargs: The keyword arguments for generate_content_stream.
        callbacks: A dict containing optional callbacks:
                   'on_chunk': func(text)
                   'on_thought': func(text)
                   'on_finish': func()
                   'on_error': func(error_message)
                   'status_message': str (optional custom status message)
        job_id (int, optional): The job ID to use. If None, a new one is reserved.
        job_name (str, optional): The name of the job. Used if job_id is None.
        target_func (callable, optional): A specific callable to run instead of
                                          client.models.generate_content_stream.
    """
    global _JOB_COUNTER, _ACTIVE_JOBS, _JOB_CLIENTS

    if job_id is None:
        # Increment Job Counter for unique ID
        job_id = reserve_next_job_id(job_name, client)
    elif client is None:
        client = _JOB_CLIENTS.get(job_id)
        if job_name != "Unknown":
            _JOB_NAMES[job_id] = job_name

    # If client is still None, attempt to get global default client
    if client is None:
        client = get_client()

    _ACTIVE_JOBS[job_id] = callbacks

    # Note: We do NOT clear _JOB_QUEUE here, to allow concurrent jobs.

    thread = threading.Thread(
        target=_job_worker,
        args=(client, kwargs, job_id, target_func),
        daemon=True
    )
    thread.start()

    # Start the timer in Vim to poll the queue
    vim.command("call ViminiInternalStartJobTimer()")

def continue_async_job(job_id, prompt, callbacks):
    """
    Continues an existing async job by sending additional prompts reusing the same client.
    """
    kwargs = create_generation_kwargs(contents=prompt)
    start_async_job(None, kwargs, callbacks, job_id=job_id, job_name=f"Continue: {prompt[:30]}...")

def _handle_response_stream(job_id, response_stream):
    for chunk in response_stream:
        if hasattr(chunk, 'candidates') and not chunk.candidates:
            continue
        # Handle cases where content is blocked or empty
        try:
            # Standard GenerateContentResponse parsing
            if hasattr(chunk, 'candidates'):
                candidate = chunk.candidates[0]
                if not candidate.content or not candidate.content.parts:
                    continue
                for part in candidate.content.parts:
                    if hasattr(part, 'thought_signature') and part.thought_signature:
                        continue

                    if not part.text:
                        continue

                    is_thought = hasattr(part, 'thought') and part.thought
                    msg_type = 'thought' if is_thought else 'chunk'
                    _JOB_QUEUE.put((job_id, msg_type, part.text))
            elif hasattr(chunk, 'text'):
                 # Fallback for simple text chunks if structure varies
                 _JOB_QUEUE.put((job_id, 'chunk', chunk.text))

        except Exception:
            pass # Skip problematic chunks

def _job_worker(client, kwargs, job_id, target_func=None):
    try:
        if target_func:
            count = 0
            while True:
                (next_count, response) = target_func(count, **kwargs)
                if next_count == 0:
                    _JOB_QUEUE.put((job_id, 'error', response))
                    break
                count = next_count
                if response:
                    _handle_response_stream(job_id, response)
                time.sleep(0.2)
        else:
            response_stream = client.models.generate_content_stream(**kwargs)
            _handle_response_stream(job_id, response_stream)

        _JOB_QUEUE.put((job_id, 'finish', None))

    except Exception as e:
        _JOB_QUEUE.put((job_id, 'error', str(e)))

def process_queue():
    """Called by Vim timer to process updates from the thread."""
    status_update = None

    while True:
        try:
            job_id, msg_type, data = _JOB_QUEUE.get_nowait()
        except queue.Empty:
            break

        callbacks = _ACTIVE_JOBS.get(job_id)
        if not callbacks:
            # Job might have been removed or is stale
            continue

        status_message = callbacks.get('status_message', "Processing...")

        # Prepend Job ID
        display_status = f"[{job_id}] {status_message}"

        if msg_type == 'chunk':
            if 'on_chunk' in callbacks:
                callbacks['on_chunk'](data)
                status_update = (display_status, False)

        elif msg_type == 'thought':
            if 'on_thought' in callbacks:
                callbacks['on_thought'](data)
                status_update = (display_status, False)

        elif msg_type == 'error':
            error_msg = f"[{job_id}] Error: {data}"
            status_update = (f"[{job_id}] {error_msg}", True)

            if 'on_error' in callbacks:
                callbacks['on_error'](data)
            else:
                status_update = (f"[{job_id}] {error_msg}", True)

            if job_id in _ACTIVE_JOBS:
                del _ACTIVE_JOBS[job_id]
            if job_id in _JOB_NAMES:
                del _JOB_NAMES[job_id]

        elif msg_type == 'finish':
            status_update = (f"[{job_id}] Finished.", False)

            if 'on_finish' in callbacks:
                callbacks['on_finish']()

            if job_id in _ACTIVE_JOBS:
                del _ACTIVE_JOBS[job_id]
            if job_id in _JOB_NAMES:
                del _JOB_NAMES[job_id]

    if status_update:
        display_message(status_update[0], error=status_update[1])

    # If no more active jobs, stop the timer
    if not _ACTIVE_JOBS:
        vim.command("call ViminiInternalStopJobTimer()")

def create_thoughts_buffer(job_id):
    """
    Creates a new buffer for displaying Vimini thoughts.

    Args:
        job_id (int): The unique ID of the job.

    Returns:
        int: The buffer number of the newly created buffer.
    """
    new_split()

    # Create a unique filename using the job_id.
    # We stop using time for uniqueness as requested.
    filename = f"[{job_id}] Vimini Thoughts"

    # Escape spaces for the Vim command
    safe_filename = filename.replace(' ', '\\ ')

    vim.command(f"file {safe_filename}")
    vim.command("setlocal buftype=nofile")
    vim.command("setlocal bufhidden=wipe")
    vim.command("setlocal noswapfile")
    vim.command("setlocal filetype=markdown")

    return vim.current.buffer.number

def append_to_buffer(buffer_number, text):
    """Helper to append text to a buffer without switching windows if possible."""
    if buffer_number == -1: return

    buf = None
    for b in vim.buffers:
        if b.number == buffer_number:
            buf = b
            break
    if not buf: return

    try:
        # Split text by newlines
        lines = text.split('\n')

        # Append to the last line
        if len(buf) > 0:
            buf[-1] += lines[0]
        else:
            buf[:] = [lines[0]]

        # Append remaining lines
        if len(lines) > 1:
            buf.append(lines[1:])

        # If the buffer is in the current window, scroll to bottom
        if vim.current.buffer.number == buffer_number:
            vim.command("normal! G")
            # vim.command("redraw") # Redraw handled by display_message usually
    except Exception:
        pass

def show_status():
    log_info("show_status()")
    # Find buffer
    buf = None
    target_name = _STATUS_BUFFER_NAME

    # Iterate buffers to find by name
    for b in vim.buffers:
        # b.name is full path. We check basename.
        if b.name and os.path.basename(b.name) == target_name:
            buf = b
            break

    if buf:
        # Check if visible in current tab
        win_nr = int(vim.eval(f"bufwinnr({buf.number})"))
        if win_nr != -1:
            vim.command(f"{win_nr}wincmd w")
        else:
            # If hidden or in another tab, we split in current tab
            new_split()
            vim.command(f"buffer {buf.number}")
    else:
        new_split()
        safe_name = target_name.replace(' ', '\\ ')
        vim.command(f"file {safe_name}")
        vim.command("setlocal buftype=nofile")
        vim.command("setlocal bufhidden=hide")
        vim.command("setlocal noswapfile")
        vim.command("setlocal filetype=text")
        # Add autocmd to restart timer when window is re-entered
        vim.command("autocmd BufWinEnter <buffer> call ViminiInternalStartStatusTimer()")
        buf = vim.current.buffer

    update_status_buffer()
    vim.command("call ViminiInternalStartStatusTimer()")

def update_status_buffer():
    # Find buffer
    buf = None
    target_name = _STATUS_BUFFER_NAME
    for b in vim.buffers:
        if b.name and os.path.basename(b.name) == target_name:
            buf = b
            break

    if not buf:
        vim.command("call ViminiInternalStopStatusTimer()")
        return

    # Check visibility
    win_nr = int(vim.eval(f"bufwinnr({buf.number})"))
    if win_nr == -1:
        # If not visible in current tab, do not update, but keep timer running
        # so it updates when we switch back to the tab with status window.
        return

    lines = [
        f"{_STATUS_BUFFER_NAME}",
        "===========================",
        ""
    ]

    if not _ACTIVE_JOBS:
        lines.append("No active jobs.")
    else:
        for job_id, callbacks in _ACTIVE_JOBS.items():
            status = callbacks.get('status_message', 'Running...')
            job_name = _JOB_NAMES.get(job_id, "Unknown")
            lines.append(f"Job ID: {job_id}")
            lines.append(f"Name: {job_name}")
            lines.append(f"Status: {status}")
            lines.append("-" * 20)

    # Update content safely
    vim.command(f"call setbufvar({buf.number}, '&modifiable', 1)")
    try:
        # Replacing buffer content
        buf[:] = lines
    finally:
        vim.command(f"call setbufvar({buf.number}, '&modifiable', 0)")
