import vim
import os, subprocess, time, io, mimetypes
from google import genai
from google.genai import types

# Module-level variables to store the API key, model name, and client instance.
_API_KEY = None
_MODEL = None
_GENAI_CLIENT = None # Global, lazily-initialized client.

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

def get_git_repo_root():
    """
    Finds the root directory of the git repository for the current buffer.
    Returns the repository root path on success, or None on failure.
    """
    current_file_path = vim.current.buffer.name
    if not current_file_path:
        vim.command("echoerr '[Vimini] Cannot determine git repository from an unnamed buffer.'")
        return None

    # Determine the root of the git repository from the current file's directory.
    start_dir = os.path.dirname(current_file_path) or '.'
    rev_parse_cmd = ['git', '-C', start_dir, 'rev-parse', '--show-toplevel']

    repo_path_result = subprocess.run(
        rev_parse_cmd,
        capture_output=True,
        text=True,
        check=False
    )

    if repo_path_result.returncode != 0:
        error_message = (repo_path_result.stderr or "Not a git repository.").strip().replace("'", "''")
        vim.command(f"echoerr '[Vimini] Git error: {error_message}'")
        return None

    return repo_path_result.stdout.strip()

def upload_context_files(client):
    """
    Uploads all relevant, named Vim buffers as context files for the API.
    Returns a list of active file handlers, or None on failure.
    """
    uploaded_files = []
    files_to_process = [] # Holds files during the PROCESSING state.

    # Upload all relevant, named buffers as context files for the API.
    vim.command("echo '[Vimini] Uploading context files...'")
    vim.command("redraw")

    for buf in vim.buffers:
        buf_name = buf.name
        if not buf_name:
            continue

        base_name = os.path.basename(buf_name)
        if base_name.startswith('Vimini '):
            continue

        buf_number = buf.number
        buf_buftype = vim.eval(f"getbufvar({buf_number}, '&buftype')")
        if buf_buftype in ['terminal', 'help', 'prompt']:
            continue

        buf_content = "\n".join(buf[:])
        if not buf_content.strip():
            continue

        # Instead of creating a temporary file, create an in-memory BytesIO object.
        buf_content_bytes = buf_content.encode('utf-8')
        buf_io = io.BytesIO(buf_content_bytes)

        # Detect mimetype from filename, default to text/plain if not found.
        mime_type, _ = mimetypes.guess_type(buf_name)
        if not mime_type:
            mime_type = 'text/plain'

        # Use the new Files API. `display_name` is kept for API to name the file correctly.
        uploaded_file = client.files.upload(
            file=buf_io,
            config=types.UploadFileConfig(
                display_name=base_name,
                mime_type=mime_type,
            ),
        )
        files_to_process.append(uploaded_file)

    # Wait for all files to be processed in a separate loop with a timeout.
    if files_to_process:
        pending_files = list(files_to_process)
        start_time = time.time()
        timeout = 2.0

        while pending_files:
            if time.time() - start_time > timeout:
                vim.command(f"echoerr '[Vimini] File processing timed out after {timeout}s. Aborting.'")
                return None # Failure

            remaining_time = timeout - (time.time() - start_time)
            vim.command(f"echo '[Vimini] Waiting for {len(pending_files)} files... ({remaining_time:.1f}s left)'")
            vim.command("redraw")
            time.sleep(0.1)

            still_pending = []
            for f in pending_files:
                updated_file = client.files.get(name=f.name)
                if updated_file.state.name == 'PROCESSING':
                    still_pending.append(updated_file)
                elif updated_file.state.name == 'ACTIVE':
                    uploaded_files.append(updated_file) # Ready
                else: # FAILED or other terminal state
                    vim.command(f"echoerr '[Vimini] File processing failed for {updated_file.display_name}. Aborting.'")
                    return None # Failure

            pending_files = still_pending

    vim.command("echo ''") # Clear message
    return uploaded_files
