import vim
import threading
import uuid
import queue
from google.genai import types
from vimini import util

# --- Autocomplete state ---
_autocomplete_lock = threading.Lock()
_current_autocomplete_job_id = None
_autocomplete_queue = queue.Queue() # Queue for thread communication
_original_cursor_hl = {} # Stores original cursor highlight settings

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

    util.log_info(f"autocomplete(verbose={verbose})")

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
