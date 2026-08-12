import vim
import json
import os
from vimini import util
from vimini.code import _DIFF_SEPARATOR, _process_x_diff_chunks

WAITING_MSG = "Waiting for prompt (CTRL-W q to exit)"
WELCOME_MSG = "Welcome to Vimini! Waiting for prompt (CTRL-W q to exit)"

chat_session = {
    'buf_num': -1,
    'running': False
}

send_buffer = ""

Q_prefix = "Q: "
A_prefix = "A: "

SPECIAL_MAP_KEYS = {
    '<': '<LT>',
    '|': '<Bar>',
    '\\': '<Bslash>',
    ' ': '<Space>'
}

def _display_send_buffer():
    global send_buffer
    try:
        current_buf = vim.current.buffer.number
        if chat_session.get('buf_num') != -1 and current_buf != chat_session.get('buf_num'):
            return
        if chat_session.get('running'):
            return
        msg = f"Prompt: {send_buffer}"
        safe_msg = msg.replace("'", "''")
        vim.command("redraw")
        vim.command(f"echo '{safe_msg}'")
    except Exception as e:
        util.log_info(f"Error displaying send buffer: {e}")

def _on_key_code(code):
    global send_buffer
    if chat_session.get('running'):
        return
    send_buffer += chr(code)
    _display_send_buffer()

def _on_backspace():
    global send_buffer
    if chat_session.get('running'):
        return
    if send_buffer:
        send_buffer = send_buffer[:-1]
        _display_send_buffer()

def _on_enter():
    global send_buffer
    if chat_session.get('running'):
        return
    prompt = send_buffer
    send_buffer = ""
    _display_send_buffer()
    if prompt.strip():
        _send_prompt(prompt)

def _setup_chat_mappings():
    vim.command("nnoremap <buffer> <silent> <CR> :py3 from vimini.chat import _on_enter; _on_enter()<CR>")
    vim.command("nnoremap <buffer> <silent> <BS> :py3 from vimini.chat import _on_backspace; _on_backspace()<CR>")
    vim.command("nnoremap <buffer> <silent> <Del> :py3 from vimini.chat import _on_backspace; _on_backspace()<CR>")

    for code in range(32, 127):
        ch = chr(code)
        if ch in SPECIAL_MAP_KEYS:
            lhs = SPECIAL_MAP_KEYS[ch]
        else:
            lhs = ch
        vim.command(f"nnoremap <buffer> <silent> {lhs} :py3 from vimini.chat import _on_key_code; _on_key_code({code})<CR>")

def _unsetup_chat_mappings():
    vim.command("silent! nunmap <buffer> <CR>")
    vim.command("silent! nunmap <buffer> <BS>")
    vim.command("silent! nunmap <buffer> <Del>")

    for code in range(32, 127):
        ch = chr(code)
        if ch in SPECIAL_MAP_KEYS:
            lhs = SPECIAL_MAP_KEYS[ch]
        else:
            lhs = ch
        vim.command(f"silent! nunmap <buffer> {lhs}")

def _enable_chat_mappings():
    buf_num = chat_session.get("buf_num", -1)
    if buf_num == -1:
        return
    try:
        winid = int(vim.eval(f"bufwinid({buf_num})"))
        if winid != -1:
            if int(vim.eval("exists('*win_execute')")):
                vim.command(f"call win_execute({winid}, 'py3 from vimini.chat import _setup_chat_mappings; _setup_chat_mappings()')")
            elif vim.current.buffer.number == buf_num:
                _setup_chat_mappings()
        elif vim.current.buffer.number == buf_num:
            _setup_chat_mappings()
    except Exception as e:
        util.log_info(f"Error enabling chat mappings: {e}")

def _on_buf_enter():
    if not chat_session.get('running'):
        _setup_chat_mappings()
    _display_send_buffer()

def _send_channel_request(req_dict):
    if not vim.eval("exists('g:vimini_channel') && type(g:vimini_channel) == v:t_channel && ch_status(g:vimini_channel) ==# 'open'"):
        util.display_message("Error: Agent server channel is not open.", error=True)
        return False
    try:
        util.log_info(f"Sending response to agent: {req_dict}")
        safe_json = json.dumps(req_dict)
        vim.command(f"call ch_sendexpr(g:vimini_channel, json_decode({json.dumps(safe_json)}))")
        return True
    except Exception as e:
        util.display_message(f"Error sending channel request: {e}", error=True)
        return False

def send_agent_approval(approved):
    req = {
        "jsonrpc": "2.0",
        "id": "chat_session",
        "method": "chat",
        "params": {
            "approved": bool(approved)
        }
    }
    _send_channel_request(req)

def send_chat_termination():
    req = {
        "jsonrpc": "2.0",
        "id": "chat_session",
        "method": "chat",
        "params": {
            "terminate": True
        }
    }
    _send_channel_request(req)

def _on_chat_buffer_closed():
    global send_buffer
    send_buffer = ""
    chat_session['running'] = False
    try:
        send_chat_termination()
        util.display_message("Chat session has been terminated.", history=True)
    except Exception as e:
        util.log_info(f"Error in _on_chat_buffer_closed: {e}")

def _on_patch_buffer_closed():
    try:
        handled = int(vim.eval("get(b:, 'vimini_patch_handled', 0)"))
        if handled:
            return
        vim.command("let b:vimini_patch_handled = 1")
        util.display_message("Patch buffer closed without applying. Canceling operation...", history=True)
        send_agent_approval(False)
    except Exception as e:
        util.log_info(f"Error in _on_patch_buffer_closed: {e}")

def _open_patch_buffer(temp_file):
    if not temp_file or not os.path.exists(temp_file):
        util.display_message("Error: Patch temp file does not exist.", error=True)
        send_agent_approval(False)
        return

    diff_content = ""
    try:
        with open(temp_file, 'r', encoding='utf-8') as f:
            diff_content = f.read()
    except Exception as e:
        util.log_info(f"Error reading patch temp file: {e}")

    if not diff_content.strip():
        util.display_message("Error: Patch content is empty.", error=True)
        send_agent_approval(False)
        return

    project_root = util.get_git_repo_root() or vim.eval("getcwd()")

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
                p = l[4:].split('\t')[0].strip()
                if p != "/dev/null":
                    if p.startswith("a/"): p = p[2:]
                    rel_path = p
                    break
            elif l.startswith("+++ "):
                p = l[4:].split('\t')[0].strip()
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
    job_id = util.reserve_next_job_id("Chat Patch")

    util.new_split()
    base_buffer_name = f"[{job_id}] Vimini Code"
    safe_name = base_buffer_name.replace(" ", "\\ ")
    vim.command(f"file {safe_name}")
    vim.command("setlocal buftype=nofile")
    vim.command("setlocal bufhidden=wipe")
    vim.command("setlocal noswapfile")

    buf = vim.current.buffer
    buf_num = buf.number

    safe_root = project_root.replace("'", "''")
    vim.command(f"let b:vimini_project_root = '{safe_root}'")
    vim.command(f"let b:vimini_job_id = '{job_id}'")
    vim.command("let b:vimini_is_chat_patch = 1")
    vim.command("let b:vimini_patch_handled = 0")

    summary_lines = [
        f"# Request Summary (Chat Patch - Job {job_id})",
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

    buf[:] = summary_lines + diff_content.splitlines()

    vim.command("setlocal filetype=diff")
    vim.command("autocmd BufUnload <buffer> py3 from vimini.chat import _on_patch_buffer_closed; _on_patch_buffer_closed()")

    util.display_message("Patch buffer opened. Run :ViminiApply to apply changes.", history=True)
    vim.command("redraw!")

def handle_channel_response(result):
    if not isinstance(result, dict):
        return

    status = result.get("status")
    buf_num = chat_session.get("buf_num", -1)

    if buf_num == -1:
        for b in vim.buffers:
            if b.name and os.path.basename(b.name) == "Vimini Chat":
                buf_num = b.number
                chat_session['buf_num'] = buf_num
                break

    if status == "chunk":
        text = result.get("text", "")
        if text:
            _write_to_buffer(buf_num, text, append_to_last=True)

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

        _write_to_buffer(buf_num, req_line, append_to_last=True)

        if tool in ("list_directory", "read_file"):
            send_agent_approval(True)
        elif tool == "apply_patch":
            _open_patch_buffer(temp_file)
        else:
            send_agent_approval(False)

    elif status in ("done", "ok"):
        chat_session["running"] = False
        text = result.get("text", "")
        if text:
            _write_to_buffer(buf_num, text, append_to_last=True)
        _write_to_buffer(buf_num, ["", WAITING_MSG])
        _enable_chat_mappings()

def _send_prompt(prompt):
    if prompt.startswith(':'):
        try:
            vim.command(prompt[1:])
        except Exception as e:
            util.display_message(f"Error: {e}", error=True)
        return

    buf_num = chat_session.get('buf_num', -1)
    if buf_num == -1:
        for b in vim.buffers:
            if b.name and os.path.basename(b.name) == "Vimini Chat":
                buf_num = b.number
                chat_session['buf_num'] = buf_num
                break

    current_buffer = None
    for b in vim.buffers:
        if b.number == buf_num:
            current_buffer = b
            break

    if current_buffer is None:
        return

    if len(current_buffer) > 0 and current_buffer[-1] in (WAITING_MSG, WELCOME_MSG):
        vim.command(f"call setbufvar({buf_num}, '&modifiable', 1)")
        try:
            if len(current_buffer) == 1:
                current_buffer[:] = []
            else:
                del current_buffer[-1]
        finally:
            vim.command(f"call setbufvar({buf_num}, '&modifiable', 0)")

    last_line = current_buffer[-1] if len(current_buffer) > 0 else ""
    lines_to_add = []
    if last_line != "":
        lines_to_add.append("")

    lines_to_add.append(f"{Q_prefix}{prompt}")
    lines_to_add.append("---")
    lines_to_add.append(A_prefix)

    _write_to_buffer(buf_num, lines_to_add)

    chat_session['running'] = True
    _unsetup_chat_mappings()

    req = {
        "jsonrpc": "2.0",
        "id": "chat_session",
        "method": "chat",
        "params": {
            "prompt": prompt
        }
    }

    if not _send_channel_request(req):
        chat_session['running'] = False
        _write_to_buffer(buf_num, ["", "[Error: Agent channel is not open]", "", WAITING_MSG])
        _enable_chat_mappings()
    else:
        util.display_message("Command has been sent and waiting for chat response")

def chat():
    global chat_session

    util.log_info("chat()")

    win_nr = vim.eval("bufwinnr('^Vimini Chat$')")

    if int(win_nr) > 0:
        vim.command(f"{win_nr}wincmd w")
    else:
        util.new_split()
        vim.command('file Vimini Chat')
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
        vim.command('setlocal nomodifiable')
        vim.command("highlight default ViminiWaiting ctermfg=Green guifg=Green")
        vim.command("highlight default ViminiPrompt ctermfg=DarkBlue guifg=DarkBlue")
        vim.command("highlight default ViminiService ctermfg=Green guifg=Green cterm=italic gui=italic")
        vim.command("syntax match ViminiWaiting '^\\(Welcome.*\\|Waiting for prompt.*\\)'")
        vim.command("syntax match ViminiPrompt '^Q: .*'")
        vim.command("syntax match ViminiService '^Agent Requested: .*'")
        vim.command("autocmd BufUnload <buffer> py3 from vimini.chat import _on_chat_buffer_closed; _on_chat_buffer_closed()")
        vim.command("autocmd BufEnter <buffer> py3 from vimini.chat import _on_buf_enter; _on_buf_enter()")
        if not chat_session.get('running'):
            _setup_chat_mappings()

    current_buffer = vim.current.buffer
    buf_num = current_buffer.number
    chat_session['buf_num'] = buf_num

    if len(current_buffer) == 1 and not current_buffer[0]:
        _write_to_buffer(buf_num, [WELCOME_MSG], clear=True)
    elif not chat_session.get('running') and (len(current_buffer) == 0 or current_buffer[-1] not in (WAITING_MSG, WELCOME_MSG)):
        _write_to_buffer(buf_num, ["", WAITING_MSG])
    _display_send_buffer()

def _write_to_buffer(buf_num, content, clear=False, append_to_last=False):
    buf = None
    for b in vim.buffers:
        if b.number == buf_num:
            buf = b
            break
    if not buf:
        return

    is_active = (vim.current.buffer.number == buf_num)

    vim.command(f"call setbufvar({buf_num}, '&modifiable', 1)")
    try:
        if clear:
            buf[:] = content if isinstance(content, list) else [content]
        else:
            if append_to_last and isinstance(content, str):
                if len(buf) > 0 and buf[-1].startswith("Agent Requested:"):
                    if not content.startswith('\n'):
                        content = '\n' + content
                lines = content.split('\n')
                if len(buf) > 0:
                    buf[-1] += lines[0]
                else:
                    buf[:] = [lines[0]]
                if len(lines) > 1:
                    buf.append(lines[1:])
            else:
                if isinstance(content, str):
                    content = content.split('\n')
                buf.append(content)

        if is_active:
            vim.command("normal! G")

    except Exception as e:
        util.log_info(f"Error writing to chat buffer: {e}")
    finally:
        vim.command(f"call setbufvar({buf_num}, '&modifiable', 0)")
        if is_active:
            _display_send_buffer()
