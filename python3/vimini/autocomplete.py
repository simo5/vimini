import vim
import uuid
import json
from vimini import util

# --- Autocomplete state ---
_current_autocomplete_job_id = None
_original_cursor_hl = {}

def _send_channel_request(req_dict):
    if not vim.eval("exists('g:vimini_channel') && type(g:vimini_channel) == v:t_channel && ch_status(g:vimini_channel) ==# 'open'"):
        util.display_message("Error: Agent server channel is not open.", error=True)
        return False
    try:
        util.log_info(f"Sending request to agent: {req_dict}")
        safe_json = json.dumps(req_dict)
        vim.command(f"call ch_sendexpr(g:vimini_channel, json_decode({json.dumps(safe_json)}))")
        return True
    except Exception as e:
        util.display_message(f"Error sending channel request: {e}", error=True)
        return False

def cancel_autocomplete():
    """
    Signals that any ongoing autocomplete job should be cancelled.
    This is called from Vimscript when the user types or leaves insert mode.
    """
    global _current_autocomplete_job_id
    _current_autocomplete_job_id = None

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
        except vim.error:
            pass
        finally:
            # Ensure the popup is always closed, no matter what key was pressed.
            vim.eval(f"popup_close({popup_id})")
            # Redraw to clear any screen artifacts from the popup.
            vim.command("redraw!")

    except Exception as e:
        error_message = str(e).replace("'", "''")
        vim.command(f"echoerr '[Vimini] Autocomplete popup Error: {error_message}'")

def handle_channel_response(result):
    """
    Callback function invoked when the agent sends back an autocomplete response via Vim's channel.
    """
    global _current_autocomplete_job_id

    if _current_autocomplete_job_id is None:
        return

    _current_autocomplete_job_id = None

    if not isinstance(result, dict):
        return

    if "error" in result:
        err_msg = result["error"].get("message", "Unknown error")
        util.log_info(f"Autocomplete error: {err_msg}")
        return

    text = result.get("text", "")
    if not text:
        return

    suggestion = text.strip().split('\n')[0]
    if not suggestion:
        return

    _show_autocomplete_popup(suggestion)

def autocomplete():
    """
    Gets context from the current buffer and sends an autocomplete request
    to the agent server via Vim's channel.
    """
    global _current_autocomplete_job_id

    util.log_info(f"autocomplete called")

    if _original_cursor_hl:
        return

    job_id = uuid.uuid4()
    _current_autocomplete_job_id = job_id

    buffer_content = list(vim.current.buffer)
    cursor_pos = vim.current.window.cursor
    row, col = cursor_pos  # `row` is 1-based.

    start_line_index = max(0, row - 20)  # Use more context
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

    req = {
        "jsonrpc": "2.0",
        "id": str(job_id),
        "method": "autocomplete",
        "params": {
            "prompt": prompt
        }
    }

    _send_channel_request(req)
