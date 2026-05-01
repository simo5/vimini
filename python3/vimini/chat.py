import vim
import json
import os
import time
from vimini import util
from google.genai import types

# Global variable to hold the chat session
chat_session = {}

Q_prefix = "Q: "
A_prefix = "A: "

# Define agent tools for safe execution
agent_tools = [
    types.Tool(
        function_declarations=[
            types.FunctionDeclaration(
                name='apply_patch',
                description='Applies a unified diff patch to modify files. ' +
                    'Ensure the patch paths are relative to the project root ' +
                    'directory. Assume patch -p1 will be used. ' +
                    'Include sufficient unmodified context lines for the patch to apply cleanly.',
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        'diff_content': types.Schema(
                            type=types.Type.STRING,
                            description='The unified diff patch to apply.'
                        )
                    },
                    required=['diff_content']
                )
            ),
            types.FunctionDeclaration(
                name='read_file',
                description='Reads the content of a file. Only files within the current working directory or its subdirectories can be read.',
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        'filepath': types.Schema(
                            type=types.Type.STRING,
                            description='Path to the file to read.'
                        )
                    },
                    required=['filepath']
                )
            ),
            types.FunctionDeclaration(
                name='list_directory',
                description='Reads the list of files and directories in a given path. Cannot list above the current working directory.',
                parameters=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        'directory_path': types.Schema(
                            type=types.Type.STRING,
                            description='The relative path to the directory to list. Defaults to "." for the current directory.'
                        )
                    }
                )
            )
        ]
    )
]

def safe_apply_patch(diff_content):
    """
    Wrapper around apply_patch to ensure no file outside the current project directory can be touched.
    """
    from vimini.code import _DIFF_SEPARATOR

    project_root = os.path.abspath(util.get_git_repo_root() or vim.eval("getcwd()"))

    modified_files = set()
    for line in diff_content.split('\n'):
        if line.startswith('--- ') or line.startswith('+++ '):
            path_part = line[4:].split('\t')[0].strip()
            if path_part == '/dev/null':
                continue
            if path_part.startswith('a/') or path_part.startswith('b/'):
                path_part = path_part[2:]

            target_path = os.path.abspath(os.path.join(project_root, path_part))
            try:
                if os.path.commonpath([project_root, target_path]) != project_root:
                    return False, f"Security error: Attempted to modify file outside project directory: {path_part}"
            except ValueError:
                return False, f"Security error: Path resolution failed for {path_part}"

            modified_files.add(path_part)

    if not modified_files:
        return False, "No valid files found in patch to apply."

    job_id = util.reserve_next_job_id("Chat Patch")
    
    util.new_split()
    base_buffer_name = f"[{job_id}] Vimini Code"
    safe_name = base_buffer_name.replace(" ", "\\ ")
    
    vim.command(f"file {safe_name}")
    vim.command("setlocal buftype=nofile")
    vim.command("setlocal bufhidden=wipe")
    vim.command("setlocal noswapfile")
    vim.command("setlocal filetype=diff")

    diff_buffer = vim.current.buffer
    diff_buffer_num = diff_buffer.number

    vim.command(f"let b:vimini_project_root = '{project_root}'")
    vim.command(f"let b:vimini_job_id = '{job_id}'")

    lines = ["The agent wants to apply a patch to the following files:", ""]
    for f in sorted(modified_files):
        lines.append(f"- {f}")
    lines.append("")
    lines.append(_DIFF_SEPARATOR)
    lines.extend(diff_content.split('\n'))

    diff_buffer[:] = lines
    vim.command("redraw!")

    # Wait until the buffer is closed (by user applying or rejecting it)
    while True:
        exists = False
        for b in vim.buffers:
            if b.number == diff_buffer_num:
                exists = True
                break
        if not exists:
            break
        time.sleep(1)

    return True, "Patch buffer closed. User has either applied or rejected the patch."

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

        # Create the GenAI session object with Agentic config
        agent_config = types.GenerateContentConfig(
            tools=agent_tools,
            system_instruction=(
                "You are an expert autonomous coding agent and software engineer. "
                "Follow these guidelines for optimal performance:\n"
                "1. **Understand Context First:** Before proposing or applying any code changes, use `list_directory` and `read_file` tools to understand the repository structure and exact file contents. Never assume or guess code.\n"
                "2. **Use the Patch Tool Correctly:** To modify files, use the `apply_patch` tool. Provide a valid unified diff. Use file paths relative to the project root. Ensure your diff includes sufficient unmodified context lines for reliable application.\n"
                "3. **Patch Reliability:** `apply_patch` should ideally be the final action in your response. If a patch fails due to a formatting or context mismatch, do not blindly retry the exact same patch. First, re-read the file to obtain the up-to-date content, then formulate a corrected diff.\n"
                "4. **Limit Retries:** Avoid multiple calls to `apply_patch` for the same file in a single response. If you struggle to apply a patch, stop and ask the user to refine the request, or request more context to ensure a higher chance of success on the next attempt.\n"
                "5. **Be Concise:** Provide brief, clear explanations. Avoid unnecessary conversational filler."
            )
        )
        chat_session['session'] = client.chats.create(
            model=util._MODEL,
            config=agent_config
        )
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

                    # Intercept function calls in the stream for safe agentic workflows
                    def agentic_stream_wrapper(current_prompt):
                        response_stream = session.send_message_stream(current_prompt)
                        pending_tool_calls = []
                        for chunk in response_stream:
                            if chunk.candidates and chunk.candidates[0].content and chunk.candidates[0].content.parts:
                                modified_parts = []
                                chunk_has_func = False
                                for part in chunk.candidates[0].content.parts:
                                    if hasattr(part, 'function_call') and part.function_call:
                                        chunk_has_func = True
                                        tool_call = part.function_call
                                        pending_tool_calls.append(tool_call)
                                        try:
                                            args_str = json.dumps(dict(tool_call.args)) if tool_call.args else "{}"
                                        except Exception:
                                            args_str = str(tool_call.args)
                                        modified_parts.append(types.Part(text=f"\n[Agent requested tool execution: {tool_call.name}({args_str})]\n"))
                                    else:
                                        modified_parts.append(part)

                                if chunk_has_func:
                                    yield types.GenerateContentResponse(
                                        candidates=[
                                            types.Candidate(
                                                content=types.Content(
                                                    parts=modified_parts
                                                )
                                            )
                                        ]
                                    )
                                else:
                                    yield chunk
                            else:
                                yield chunk

                        if pending_tool_calls:
                            responses = []
                            for tool_call in pending_tool_calls:
                                if tool_call.name == 'list_directory':
                                    from vimini.context import list_directory
                                    try:
                                        args_dict = dict(tool_call.args) if tool_call.args else {}
                                    except Exception:
                                        args_dict = {}
                                    dir_path = args_dict.get('directory_path', '.')
                                    result_text = list_directory(dir_path)
                                    responses.append(types.Part.from_function_response(
                                        name=tool_call.name,
                                        response={'result': result_text}
                                    ))
                                elif tool_call.name == 'read_file':
                                    from vimini.context import read_file
                                    try:
                                        args_dict = dict(tool_call.args) if tool_call.args else {}
                                    except Exception:
                                        args_dict = {}
                                    filepath = args_dict.get('filepath', '')
                                    result_text = read_file(filepath)
                                    responses.append(types.Part.from_function_response(
                                        name=tool_call.name,
                                        response={'result': result_text}
                                    ))
                                elif tool_call.name == 'apply_patch':
                                    try:
                                        args_dict = dict(tool_call.args) if tool_call.args else {}
                                    except Exception:
                                        args_dict = {}
                                    diff_content = args_dict.get('diff_content', '')
                                    success, result_text = safe_apply_patch(diff_content)
                                    responses.append(types.Part.from_function_response(
                                        name=tool_call.name,
                                        response={'result': result_text}
                                    ))
                                else:
                                    # TODO: Suspend stream, prompt user for confirmation, execute tool, and submit function_response
                                    responses.append(types.Part.from_function_response(
                                        name=tool_call.name,
                                        response={'error': 'Tool execution pending user confirmation (not implemented)'}
                                    ))

                            yield from agentic_stream_wrapper(responses)

                    # Return the new counter as 'prev' for next iteration
                    return (c, agentic_stream_wrapper(prompt))
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
