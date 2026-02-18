import vim
from vimini import util

# Global variable to hold the chat session
chat_session = {}

Q_prefix = "Q: "
A_prefix = "A: "

def chat(prompt=None):
    """
    Opens a new chat window named "Vimini Chat" if one does not yet exist,
    otherwise switches to that window. If a non-empty argument is provided,
    it is used as a chat query. Code runs asynchronously.
    """
    global chat_session

    # Initialize chat_session structure if needed
    if not chat_session or 'session' not in chat_session:
        chat_session = {
            'prompt': '',
            'session': None,
            'counter': 0,
            'buf_num': -1,
            'running': False
        }

    if prompt:
        prompt = prompt.strip().strip("'\"").strip()
        chat_session['prompt'] = prompt
        chat_session['counter'] += 1

    util.log_info(f"chat({prompt})")

    # --- 1. Find or create the chat window ---
    win_nr = vim.eval("bufwinnr('^Vimini Chat$')")

    if int(win_nr) > 0:
        vim.command(f"{win_nr}wincmd w")
    else:
        # Create and initialize a new chat window
        util.new_split()
        vim.command('file Vimini Chat')
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
        vim.command('setlocal nomodifiable') # Read only by default

        session = chat_session.get('session')
        if session:
            buf_num = vim.current.buffer.number
            _write_to_buffer(buf_num, ["History:"], clear=True)
            for msg in session.get_history():
                _write_to_buffer(buf_num, [f"{msg.role}: {msg.parts[0].text}"])

    current_buffer = vim.current.buffer
    buf_num = current_buffer.number

    # Update buffer number in session so the running thread knows where to write
    chat_session['buf_num'] = buf_num

    # --- 2. Initialize buffer if it's empty ---
    if len(current_buffer) == 1 and not current_buffer[0]:
        _write_to_buffer(buf_num, [""], clear=True)

    if not prompt:
        return

    # --- 3. Prepare for Async Job ---
    # Add spacing if needed
    last_line = current_buffer[-1]
    lines_to_add = []
    if last_line != "":
        lines_to_add.append("")

    lines_to_add.append(f"{Q_prefix}{prompt}")
    lines_to_add.append("---")
    lines_to_add.append(A_prefix)

    _write_to_buffer(buf_num, lines_to_add)

    # If job is already running, the updated counter/prompt will trigger action in the loop
    if chat_session.get('running'):
        return

    try:
        # Ensure session exists
        client = util.get_client()
        if not client:
             return

        # Create the GenAI session object
        chat_session['session'] = client.chats.create(model=util._MODEL)
        chat_session['running'] = True

        def on_chunk(text):
            _write_to_buffer(chat_session['buf_num'], text, append_to_last=True)

        def on_finish():
            # nuke this session
            chat_session['session'] = None
            chat_session['running'] = False
            _write_to_buffer(chat_session['buf_num'], ["", "Terminated"])

        def on_error(msg):
            _write_to_buffer(chat_session['buf_num'], [f"\n[Error: {msg}]"])
            chat_session['session'] = None
            chat_session['running'] = False
            return f"Chat Error: {msg}"

        # Target function that trigers only on new updates
        def target(prev, **kwargs):
            if chat_session.get('running', False):
                c = chat_session.get('counter', 0)
                if c > prev:
                    # New prompt found
                    session = chat_session.get('session')
                    if not session:
                        return (0, "Invalid session")
                    prompt = chat_session.get('prompt')
                    if not prompt:
                        return (0, "Invalid prompt")
                    # Return the new counter as 'prev' for next iteration
                    return (c, session.send_message_stream(prompt))
                else:
                    return (c, None)
            return (0, "Session not running")

        util.start_async_job(
            client,
            kwargs={}, # No kwargs needed for the lambda as we captured prompt
            callbacks={
                'on_chunk': on_chunk,
                'on_error': on_error,
                'on_finish': on_finish,
                'status_message': "Running..."
            },
            job_name="Persistent Vimini Chat",
            target_func=target
        )

    except Exception as e:
        util.display_message(f"Error starting chat: {e}", error=True)
        _write_to_buffer(buf_num, ["", f"[System Error: {e}]"])

def _write_to_buffer(buf_num, content, clear=False, append_to_last=False):
    """
    Writes content to a read-only buffer by temporarily enabling modifiable.
    Args:
        buf_num (int): Buffer number.
        content (str or list): Text to write. If string, handled based on append_to_last.
        clear (bool): If True, replaces buffer content.
        append_to_last (bool): If True, appends string content to the very last line
                               (and adds new lines if content has them).
    """
    buf = None
    for b in vim.buffers:
        if b.number == buf_num:
            buf = b
            break
    if not buf: return

    # Determine if we need to scroll (only if active window)
    is_active = (vim.current.buffer.number == buf_num)

    vim.command(f"call setbufvar({buf_num}, '&modifiable', 1)")
    try:
        if clear:
             buf[:] = content if isinstance(content, list) else [content]
        else:
            if append_to_last and isinstance(content, str):
                # Split content into lines to handle newlines correctly
                lines = content.split('\n')

                # Append first part to the last line of buffer
                if len(buf) > 0:
                    buf[-1] += lines[0]
                else:
                    buf[:] = [lines[0]]

                # Append subsequent lines if any
                if len(lines) > 1:
                    buf.append(lines[1:])
            else:
                # Append list of lines or single string as new line
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
             vim.command("redraw")
