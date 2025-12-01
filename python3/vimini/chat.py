import vim
from vimini import util
from google import genai

# Global variable to hold chat sessions, mapping buffer numbers to ChatSession objects.
_chat_sessions = {}

Q_prefix = "Q: "
A_prefix = "A: "

def _process_chat_entry():
    """
    Processes the current line as a chat query, sends it to the model,
    and prepares for the next query. This function is designed to be
    called from a buffer-local mapping.
    """
    try:
        client = util.get_client()
        if not client:
            return

        buf_num = vim.current.buffer.number
        buflines = vim.current.buffer[:]
        line_num, _ = vim.current.window.cursor
        current_line_index = line_num - 1

        user_prompt_full = buflines[current_line_index]
        if not user_prompt_full.startswith(Q_prefix):
            return

        user_prompt = user_prompt_full[3:].strip()
        util.log_info(f"chat user_prompt({user_prompt}), len= {len(user_prompt)}")
        if not user_prompt or len(user_prompt) == 0:
            # Go back to insert mode and wait for user to type a proper query
            vim.command('startinsert!')
            return

        util.display_message("Querying model...")
        # Unmap <CR> to prevent recursion while streaming the answer
        vim.command('iunmap <buffer> <CR>')

        # Insert separator and answer prefix *after* the current line
        insertion_point = current_line_index + 1
        vim.current.buffer[insertion_point:insertion_point] = ['---', A_prefix]
        answer_line_index = insertion_point + 1

        vim.current.window.cursor = (answer_line_index + 1, 3) # +1 for 1-based cursor
        vim.command("redraw")

        # --- Get or create ChatSession ---
        if buf_num not in _chat_sessions:
            # Session lost (e.g., Vim restart), rebuild from buffer.
            # History is rebuilt up to the line *before* the current prompt.
            _chat_sessions[buf_num] = client.chats.create(model=util._MODEL)

        chat_session = _chat_sessions[buf_num]
        response_stream = chat_session.send_message_stream(user_prompt)

        # Stream response into the buffer
        for chunk in response_stream:
            chunk_text = ""
            try:
                # Accessing chunk.text can raise ValueError if the response is
                # blocked, or AttributeError for non-text chunks.
                chunk_text = chunk.text
            except (ValueError, AttributeError):
                # Simply skip chunks that cause errors or have no text.
                pass

            if not chunk_text:
                continue

            lines_to_add = chunk_text.split('\n')
            # Append first part of the chunk to the last line of the answer
            vim.current.buffer[answer_line_index] += lines_to_add[0]

            # Insert the rest as new lines
            if len(lines_to_add) > 1:
                new_lines = lines_to_add[1:]
                vim.current.buffer[answer_line_index + 1 : answer_line_index + 1] = new_lines
                answer_line_index += len(new_lines)

            vim.current.window.cursor = (answer_line_index + 1, len(vim.current.buffer[answer_line_index]))
            vim.command("redraw")

        util.display_message("")

        # --- Prepare for next prompt ---
        vim.current.buffer.append('')
        vim.current.buffer.append(Q_prefix)
        vim.current.window.cursor = (len(vim.current.buffer), 4)

        vim.command('inoremap <buffer> <silent> <CR> <Esc>:py3 import vimini.chat; vimini.chat._process_chat_entry()<CR>')
        vim.command('startinsert!')

    except Exception as e:
        util.display_message(f"Error during chat processing: {e}", error=True)
        # Attempt to restore mapping on error
        vim.command('inoremap <buffer> <silent> <CR> <Esc>:py3 import vimini.chat; vimini.chat._process_chat_entry()<CR>')


def chat(prompt):
    """
    Opens a new chat window named "Vimini Chat" if one does not yet exist,
    otherwise switches to that window. If a non-empty argument is provided,
    it is used as a chat query. Otherwise, the editor switches to insert
    mode for the user to type a query.
    """
    if prompt:
        prompt = prompt.strip().strip("'\"").strip()

    util.log_info(f"chat({prompt})")
    try:
        # --- 1. Find or create the chat window ---
        win_nr = vim.eval("bufwinnr('^Vimini Chat$')")

        if int(win_nr) > 0:
            vim.command(f"{win_nr}wincmd w")
        else:
            # Create and initialize a new chat window
            util.new_split()
            vim.command('file Vimini Chat')
            vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
            vim.command('inoremap <buffer> <silent> <CR> <Esc>:py3 import vimini.chat; vimini.chat._process_chat_entry()<CR>')

        # --- 2. Initialize buffer if it's empty ---
        # This handles the very first run for a new window.
        if not vim.current.buffer or (len(vim.current.buffer) == 1 and not vim.current.buffer[0]):
            vim.current.buffer[:] = [Q_prefix]

        # --- 3. Handle prompt or enter insert mode ---
        if prompt and len(prompt) != 0:
            vim.command('normal! G') # Go to the end of the buffer.

            # If the last line is an empty prompt, replace it. Otherwise, append.
            if vim.current.buffer[-1] == Q_prefix:
                vim.current.buffer[-1] = f"{Q_prefix}{prompt}"
            else:
                vim.current.buffer.append(f"{Q_prefix}{prompt}")

            # Set cursor to the prompt line and process it.
            vim.current.window.cursor = (len(vim.current.buffer), 1)
            _process_chat_entry()
        else:
            # No prompt provided, so prepare for interactive input.
            vim.command('normal! G') # Go to the end of the buffer.

            # If the last line is not an empty prompt, add one.
            if vim.current.buffer[-1] != Q_prefix:
                vim.current.buffer.append(Q_prefix)

            # Normalize the last line to be exactly 'Q: ' for consistency.
            vim.current.buffer[-1] = Q_prefix

            # Position cursor and enter insert mode.
            vim.current.window.cursor = (len(vim.current.buffer), len(vim.current.buffer[-1]))
            vim.command('startinsert!')

    except Exception as e:
        util.display_message(f"Error starting chat: {e}", error=True)
