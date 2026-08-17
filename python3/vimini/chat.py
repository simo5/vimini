import vim
import json
import os
from vimini import util, context
from vimini.code import _DIFF_SEPARATOR, _process_x_diff_chunks

WAITING_MSG = "Waiting for prompt (CTRL-W q to exit)"
WELCOME_MSG = "Welcome to Vimini! Waiting for prompt (CTRL-W q to exit)"

Q_prefix = "Q: "
A_prefix = "A: "

SPECIAL_MAP_KEYS = {
    '<': '<LT>',
    '|': '<Bar>',
    '\\': '<Bslash>',
    ' ': '<Space>'
}

def _to_str(val):
    if isinstance(val, bytes):
        return val.decode('utf-8', errors='replace')
    return str(val) if val is not None else ""

def _get_buffer(buf_num):
    buffer = None
    if vim.current.buffer.number == buf_num:
        buffer = vim.current.buffer
    else:
        for b in vim.buffers:
            if b.number == buf_num:
                buffer = b
                break
    return buffer

def _display_send_buffer(buffer):
    try:
        if buffer.vars.get('vimini_waiting', False):
            return
        prompt_str = _to_str(buffer.vars.get('vimini_send_buffer', ''))
        msg = f"Prompt: {prompt_str}"
        safe_msg = msg.replace("'", "''")
        vim.command("redraw")
        vim.command(f"echo '{safe_msg}'")
    except Exception as e:
        util.log_info(f"Error displaying send buffer: {e}")

def _on_key_code(buf_num, code=None):
    if code is None:
        return

    try:
        buffer = _get_buffer(buf_num)
        if buffer is None or buffer.vars.get('vimini_waiting', False):
            return
        curr = _to_str(buffer.vars.get('vimini_send_buffer', ''))
        buffer.vars['vimini_send_buffer'] = curr + chr(code)
        _display_send_buffer(buffer)
    except Exception as e:
        util.log_info(f"Error handling key code: {e}")

def _on_backspace(buf_num):
    try:
        buffer = _get_buffer(buf_num)
        if buffer is None or buffer.vars.get('vimini_waiting', False):
            return
        curr = _to_str(buffer.vars.get('vimini_send_buffer', ''))
        if curr:
            buffer.vars['vimini_send_buffer'] = curr[:-1]
            _display_send_buffer(buffer)
    except Exception as e:
        util.log_info(f"Error handling backspace: {e}")

def _on_enter(buf_num):
    try:
        buffer = _get_buffer(buf_num)
        if buffer is None or buffer.vars.get('vimini_waiting', False):
            return
        prompt = _to_str(buffer.vars.get('vimini_send_buffer', ''))
        buffer.vars['vimini_send_buffer'] = ""
        _display_send_buffer(buffer)
        if prompt.strip():
            _send_prompt(prompt, buffer)
    except Exception as e:
        util.log_info(f"Error handling enter: {e}")

def _enable_chat_mappings(buf_num):
    vim.command(f"nnoremap <buffer> <silent> <CR> :py3 from vimini.chat import _on_enter; _on_enter({buf_num})<CR>")
    vim.command(f"nnoremap <buffer> <silent> <BS> :py3 from vimini.chat import _on_backspace; _on_backspace({buf_num})<CR>")
    vim.command(f"nnoremap <buffer> <silent> <Del> :py3 from vimini.chat import _on_backspace; _on_backspace({buf_num})<CR>")

    for code in range(32, 127):
        ch = chr(code)
        if ch in SPECIAL_MAP_KEYS:
            lhs = SPECIAL_MAP_KEYS[ch]
        else:
            lhs = ch
        vim.command(f"nnoremap <buffer> <silent> {lhs} :py3 from vimini.chat import _on_key_code; _on_key_code({buf_num},{code})<CR>")

def _on_buf_enter(*args):
    try:
        buffer = vim.current.buffer
        if not buffer.vars.get('vimini_waiting', False):
            _enable_chat_mappings(buffer.number)
        _display_send_buffer(buffer)
    except Exception as e:
        util.log_info(f"failed to handle _on_buf_enter: {e}")

def send_agent_approval(approved, req_id):
    req = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "chat",
        "params": {
            "approved": bool(approved)
        }
    }
    util.send_channel_request(req, True)

def send_chat_termination(req_id):
    req = {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "chat",
        "params": {
            "terminate": True
        }
    }
    util.send_channel_request(req, True)

def _on_chat_buffer_closed(buf_num):
    try:
        buffer = _get_buffer(buf_num)
        if buffer is None:
            return
        buffer.vars["vimini_waiting"] = False
        req_id = _to_str(buffer.vars.get("vimini_job_id", ""))
        send_chat_termination(req_id)
        util.display_message("Chat session has been terminated.", history=True)
    except Exception as e:
        util.log_info(f"Error in _on_chat_buffer_closed: {e}")

def _on_patch_buffer_closed(req_id):
    try:
        handled = int(vim.eval("get(b:, 'vimini_patch_handled', 0)"))
        if handled:
            return
        vim.command("let b:vimini_patch_handled = 1")
        util.display_message("Patch buffer closed without applying. Canceling operation...", history=True)
        send_agent_approval(False, req_id)
    except Exception as e:
        util.log_info(f"Error in _on_patch_buffer_closed: {e}")

def _open_patch_buffer(temp_file, req_id):
    if not temp_file or not os.path.exists(temp_file):
        util.display_message("Error: Patch temp file does not exist.", error=True)
        send_agent_approval(False, req_id)
        return

    diff_content = ""
    try:
        with open(temp_file, 'r', encoding='utf-8') as f:
            diff_content = f.read()
    except Exception as e:
        util.log_info(f"Error reading patch temp file: {e}")

    if not diff_content.strip():
        util.display_message("Error: Patch content is empty.", error=True)
        send_agent_approval(False, req_id)
        return

    try:
        project_root = util.get_git_repo_root()
        if not project_root:
            util.display_message("Error: Patches can only be applied to git repositories.", error=True)
            project_root = os.getcwd()

        fixed_lines = []
        raw_chunks = []
        current_chunk = []
        for line in diff_content.strip('\n').split('\n'):
            if line.startswith("diff --git ") or line.startswith("--- "):
                if current_chunk and any(l.startswith("@@ ") or l.startswith("+++ ") for l in current_chunk):
                    raw_chunks.append(current_chunk)
                    current_chunk = []
            current_chunk.append(line)
        if current_chunk:
            raw_chunks.append(current_chunk)

        for chunk in raw_chunks:
            rel_path = None
            for l in chunk:
                if l.startswith("--- "):
                    p = l[4:].strip().split('\t')[0]
                    if p != "/dev/null":
                        if p.startswith("a/"): p = p[2:]
                        rel_path = p
                        break
                elif l.startswith("+++ "):
                    p = l[4:].strip().split('\t')[0]
                    if p != "/dev/null":
                        if p.startswith("b/"): p = p[2:]
                        rel_path = p
                        break
                elif l.startswith("diff --git "):
                    parts = l.split()
                    if len(parts) >= 4:
                        p = parts[3]
                        if p.startswith("b/"): p = p[2:]
                        elif p.startswith("a/"): p = p[2:]
                        rel_path = p
                        break

            abs_path = os.path.join(project_root, rel_path) if rel_path else None
            file_exists = os.path.exists(abs_path) if abs_path else True
            chunk_str = "\n".join(chunk)
            processed = _process_x_diff_chunks(chunk_str, rel_path or "", file_exists)
            if processed:
                fixed_lines.extend(processed)
            else:
                fixed_lines.extend(chunk)

        if fixed_lines:
            diff_content = "\n".join(fixed_lines) + "\n"
    except Exception as e:
        util.log_info(f"Error processing patch temp file: {e}")

    try:
        util.new_split()
        base_buffer_name = f"[{req_id}] Vimini Code"
        safe_name = base_buffer_name.replace(" ", "\\ ")
        vim.command(f"file {safe_name}")
        vim.command("setlocal buftype=nofile")
        vim.command("setlocal bufhidden=wipe")
        vim.command("setlocal noswapfile")

        buffer = vim.current.buffer
        buffer.vars["vimini_project_root"] = project_root
        buffer.vars["vimini_chat_job_id"] = req_id
        buffer.vars["vimini_is_chat_patch"] = 1
        buffer.vars["vimini_patch_handled"] = 0

        summary_lines = [
            f"# Request Summary (Chat Patch - Req Id {req_id})",
            "",
            "## Agent Patch Request",
            "The agent requested code modifications below.",
            "Run :ViminiApply to apply these changes, or close this buffer (:q) to cancel.",
            "",
            "---",
            "",
            _DIFF_SEPARATOR
        ]
        if not diff_content.endswith('\n'):
            diff_content += '\n'

        buffer[:] = summary_lines + diff_content.splitlines()

        vim.command("setlocal filetype=diff")
        vim.command(f"autocmd BufUnload <buffer> py3 from vimini.chat import _on_patch_buffer_closed; _on_patch_buffer_closed({req_id})")

        util.display_message("Patch buffer opened. Run :ViminiApply to apply changes.", history=True)
        vim.command("redraw!")
    except Exception as e:
        util.log_info(f"Error creating patch buffer: {e}")

def handle_channel_response(req_id, result):
    status = result.get("status")

    buffer = None
    try:
        for b in vim.buffers:
            job_id = b.vars.get("vimini_job_id")
            if job_id is not None and _to_str(job_id) == req_id:
                buffer = b
                break
    except Exception:
        util.display_message("Error handling channel response")
        return

    if buffer is None:
        return

    if status == "chunk":
        text = result.get("text", "")
        if text:
            _write_to_buffer(buffer, text, append_to_last=True)

    elif status == "tool_use_requested":
        tool = result.get("tool", "")
        temp_file = result.get("temp_file")

        if tool == "apply_patch":
            req_line = f"\nAgent Requested: apply_patch({temp_file})"
        else:
            args = result.get("args", {})
            if isinstance(args, dict):
                args_str = ", ".join(f"{k}={repr(v)}" for k, v in args.items())
            else:
                args_str = str(args) if args else ""
            req_line = f"\nAgent Requested: {tool}({args_str})"

        _write_to_buffer(buffer, req_line, append_to_last=True)

        if tool in ("list_directory", "read_file"):
            send_agent_approval(True, req_id)
        elif tool == "apply_patch":
            _open_patch_buffer(temp_file, req_id)
        else:
            send_agent_approval(False, req_id)

    elif status in ("done", "ok"):
        buffer.vars["vimini_waiting"] = False
        text = result.get("text", "")
        if text:
            _write_to_buffer(buffer, text, append_to_last=True)
        if vim.current.buffer.number == buffer.number:
            _write_to_buffer(buffer, ["", WAITING_MSG])
            _display_send_buffer(buffer)

    elif status == "terminated":
        buffer.vars["vimini_waiting"] = False

    elif status == "error":
        buffer.vars["vimini_waiting"] = False
        err_msg = result.get("error", "Unknown error")
        _write_to_buffer(buffer, [f"\n[Error: {err_msg}]", "", WAITING_MSG])
        if vim.current.buffer.number == buffer.number:
            _display_send_buffer(buffer)

def _send_prompt(prompt, buffer):
    if prompt.startswith(":"):
        try:
            vim.command(prompt[1:])
        except Exception as e:
            util.display_message(f"Error: {e}", error=True)
        return

    if len(buffer) > 0 and buffer[-1] in (WAITING_MSG, WELCOME_MSG):
        buffer.options["modifiable"] = 1
        try:
            if len(buffer) == 1:
                buffer[:] = []
            else:
                del buffer[-1]
        finally:
            buffer.options["modifiable"] = 0

    last_line = buffer[-1] if len(buffer) > 0 else ""
    lines_to_add = []
    if last_line != "":
        lines_to_add.append("")

    lines_to_add.append(f"{Q_prefix}{prompt}")
    lines_to_add.append("---")
    lines_to_add.append(A_prefix)

    _write_to_buffer(buffer, lines_to_add)

    buffer.vars["vimini_waiting"] = True

    req = {
        "jsonrpc": "2.0",
        "id": _to_str(buffer.vars.get("vimini_job_id", "")),
        "method": "chat",
        "params": {
            "prompt": prompt,
        }
    }

    if not util.send_channel_request(req, False):
        buffer.vars["vimini_waiting"] = False
        _write_to_buffer(buffer, ["", "[Error: Agent channel is not open]", "", WAITING_MSG])
    else:
        util.display_message("Command has been sent and waiting for chat response")

def chat():
    req_id = str(util.reserve_next_job_id("Chat"))

    util.new_split()
    buffer = vim.current.buffer
    buf_num = buffer.number
    buffer_name = f"[{req_id}] Vimini Chat"
    vim.command(f"file {buffer_name}")
    util.log_info(f"New chat in buffer <{buffer_name}>")

    vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
    vim.command('setlocal nomodifiable')
    vim.command("highlight default ViminiWaiting ctermfg=Green guifg=Green")
    vim.command("highlight default ViminiPrompt ctermfg=DarkBlue guifg=DarkBlue")
    vim.command("highlight default ViminiService ctermfg=Green guifg=Green cterm=italic gui=italic")
    vim.command("syntax match ViminiWaiting '^\\(Welcome.*\\|Waiting for prompt.*\\)'")
    vim.command("syntax match ViminiPrompt '^Q: .*'")
    vim.command("syntax match ViminiService '^Agent Requested: .*'")
    vim.command(f"autocmd BufUnload <buffer> py3 from vimini.chat import _on_chat_buffer_closed; _on_chat_buffer_closed({buf_num})")
    vim.command(f"autocmd BufEnter <buffer> py3 from vimini.chat import _on_buf_enter; _on_buf_enter({buf_num})")

    buffer.vars["vimini_job_id"] = req_id
    buffer.vars["vimini_send_buffer"] = ""
    buffer.vars["vimini_waiting"] = False

    _enable_chat_mappings(buf_num)
    _write_to_buffer(buffer, [WELCOME_MSG], clear=True)
    _display_send_buffer(buffer)

def _write_to_buffer(buffer, content, clear=False, append_to_last=False):

    #vim.command(f"call setbufvar({buffer.number}, '&modifiable', 1)")
    buffer.options["modifiable"] = 1
    try:
        if clear:
            buffer[:] = content if isinstance(content, list) else [content]
        else:
            if append_to_last and isinstance(content, str):
                if len(buffer) > 0 and buffer[-1].startswith("Agent Requested:"):
                    if not content.startswith('\n'):
                        content = '\n' + content
                lines = content.split('\n')
                if len(buffer) > 0:
                    buffer[-1] += lines[0]
                else:
                    buffer[:] = [lines[0]]
                if len(lines) > 1:
                    buffer.append(lines[1:])
            else:
                if isinstance(content, str):
                    content = content.split('\n')
                buffer.append(content)

        if vim.current.buffer.number == buffer.number:
            vim.command("normal! G")

    except Exception as e:
        util.log_info(f"Error writing to chat buffer: {e}")
    finally:
        #vim.command(f"call setbufvar({buffer.number}, '&modifiable', 0)")
        buffer.options["modifiable"] = 0
