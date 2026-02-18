import vim
import os
import re
import shlex
import subprocess
from . import util

# To store state between search and apply
RIPGREP_CONFIG_STORE = {}

def dedup_slashes(line):
    return re.sub(r'//+', '/', line)

def _parse_file_ranges(lines, context_separator):
    file_ranges = {}
    current_file = None
    current_range = None

    for line in lines:
        if not line.strip():
            continue

        if line == context_separator:
            if current_file and current_range:
                file_ranges.setdefault(current_file, []).append(current_range)
            current_range = None
            continue

        is_content_line = line.split(':', 1)[0].isdigit()

        if not is_content_line:
            line_path = dedup_slashes(line)
            if current_file: # Finish previous file
                if current_range:
                    file_ranges.setdefault(current_file, []).append(current_range)
            current_file = line_path
            current_range = None
        elif is_content_line:
            parts = line.split(':', 1)
            line_num = int(parts[0])
            if current_file:
                if current_range:
                    current_range = (current_range[0], line_num)
                else:
                    current_range = (line_num, line_num)

    if current_file and current_range:
        file_ranges.setdefault(current_file, []).append(current_range)

    return file_ranges

def _format_output_for_buffer(lines, file_ranges, context_separator):
    buffer_content = []
    first_file_written = False
    file_paths = set(file_ranges.keys())

    for line in lines:
        parts = line.split(':', 1)
        if len(parts) > 1 and parts[0].isdigit():
            buffer_content.append(parts[1])
        else:
            line = dedup_slashes(line)
            is_file_path = line in file_paths
            if is_file_path:
                if first_file_written:
                    buffer_content.append(context_separator)
                    buffer_content.append('')
                else:
                    first_file_written = True
            buffer_content.append(line)

    if first_file_written:
        buffer_content.append(context_separator)

    return buffer_content

def _parse_modified_buffer(lines, file_ranges, context_separator):
    changes = {}
    current_file = None
    current_block_lines = []
    file_paths = set(file_ranges.keys())

    for line in lines:
        line_path = dedup_slashes(line)
        if line_path in file_paths:
            current_file = line_path
            changes[current_file] = []
            current_block_lines = []
            continue

        if current_file is None:
            continue

        if line == context_separator:
            changes[current_file].append(current_block_lines)
            current_block_lines = []
        elif line.strip() == "" and not current_block_lines:
            continue
        else:
            current_block_lines.append(line)

    return changes

def _apply_changes(changes, file_ranges, project_root):
    modified_files = []
    for file_path, blocks in changes.items():
        if file_path not in file_ranges:
            continue

        ranges = file_ranges[file_path]
        if len(blocks) != len(ranges):
            util.display_message(f"Error for {file_path}: edited block count ({len(blocks)}) does not match original ({len(ranges)}).", error=True)
            return modified_files

        full_path = os.path.join(project_root, file_path)
        try:
            with open(full_path, 'r', encoding='utf-8') as f:
                original_lines = f.read().splitlines()
        except FileNotFoundError:
            original_lines = []
        except Exception as e:
            util.display_message(f"Error reading {file_path}: {e}", error=True)
            continue

        for i, (start_line, end_line) in reversed(list(enumerate(ranges))):
            start_index = start_line - 1
            end_index = end_line
            new_content_block = blocks[i]
            original_lines[start_index:end_index] = new_content_block

        try:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(original_lines) + '\n')
            modified_files.append(full_path)
        except Exception as e:
            util.display_message(f"Error writing to {file_path}: {e}", error=True)
    
    return modified_files

def search(regex, path_to_search=".", context_lines=5):
    global RIPGREP_CONFIG_STORE
    context_separator = "-- DO NOT DELETE THIS SEPARATOR --"
    try:
        cmd = [
            'rg', '-n', f'-C{context_lines}', '--heading', '--color=never',
            '--field-context-separator=:', f'--context-separator={context_separator}',
            '-e', regex, path_to_search
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, check=False, encoding='utf-8')

        if result.returncode > 1:
            err_msg = result.stderr.strip()
            if "command not found" in err_msg.lower() or "no such file" in err_msg.lower():
                 util.display_message("ripgrep command not found. Please install it.", error=True)
            else:
                 util.display_message(f"ripgrep failed: {err_msg}", error=True)
            return

        output = result.stdout
        if not output.strip():
            util.display_message("No results found.", history=True)
            return
    except FileNotFoundError:
        util.display_message("ripgrep command not found. Please install it.", error=True)
        return
    except Exception as e:
        util.display_message(f"Error running ripgrep: {e}", error=True)
        return

    lines = output.splitlines()
    file_ranges = _parse_file_ranges(lines, context_separator)
    buffer_content = _format_output_for_buffer(lines, file_ranges, context_separator)

    project_root = util.get_git_repo_root() or os.getcwd()

    RIPGREP_CONFIG_STORE = {
        'file_ranges': file_ranges,
        'context_separator': context_separator,
        'project_root': project_root
    }

    # Check if buffer already exists and delete it to avoid E95
    for buf in vim.buffers:
        if buf.name and buf.name.endswith('ViminiRipGrep'):
            vim.command(f'bwipeout! {buf.number}')
            break

    util.new_split()
    vim.command('file ViminiRipGrep')
    vim.command('setlocal buftype=nofile noswapfile')
    vim.current.buffer[:] = buffer_content
    vim.command(f"let b:vimini_project_root = '{project_root}'")

def command(arg_string):
    """
    Performs a ripgrep search with the first argument as the regex,
    and uses the rest as a prompt to modify the results with Gemini.
    """
    try:
        args = shlex.split(arg_string)
    except ValueError as e:
        util.display_message(f"Invalid arguments: {e}", error=True)
        return

    if not args:
        util.display_message("A regex pattern is required.", error=True)
        return
    regex = args[0]

    if len(args) < 2:
        util.display_message("A prompt for Gemini is required after the regex.", error=True)
        return
    prompt = " ".join(args[1:])

    search(regex)

    rg_buffer = None
    for buf in vim.buffers:
        if buf.name and buf.name.endswith('ViminiRipGrep'):
            rg_buffer = buf
            break

    if not rg_buffer:
        return

    buffer_content = "\n".join(rg_buffer[:])
    if not buffer_content.strip():
        util.display_message("Ripgrep results are empty, nothing to send to Gemini.", history=True)
        return

    client = util.get_client()
    if not client:
        return

    full_prompt = (
        "You are an expert code editor. You are given a buffer containing code snippets "
        "from a ripgrep search. Your task is to apply the user's request to this buffer.\n\n"
        "The buffer is structured with file paths as headers, followed by code snippets. "
        "There is a separator (`-- DO NOT DELETE THIS SEPARATOR --`) between results from different files. "
        "You MUST preserve this exact structure in your output.\n\n"
        "Only output the modified buffer content. Do not add any preamble, explanations, or markdown code fences.\n\n"
        f'USER REQUEST: "{prompt}"\n\n'
        "--- BUFFER CONTENT TO MODIFY ---\n"
        f"{buffer_content}\n"
        "--- END BUFFER CONTENT ---"
    )

    util.display_message("Calling Gemini to modify results... (Async)")

    job_id = util.reserve_next_job_id(f"Ripgrep: {regex}")
    rg_buffer_num = rg_buffer.number
    
    # State closure for the async callback
    is_first_chunk = True

    def on_chunk(text):
        nonlocal is_first_chunk
        if is_first_chunk:
            # Find the buffer again to ensure we have the object
            # and clear it before writing the first chunk of new content.
            for b in vim.buffers:
                if b.number == rg_buffer_num:
                    b[:] = []
                    break
            is_first_chunk = False
        
        util.append_to_buffer(rg_buffer_num, text)

    def on_error(msg):
        util.display_message(f"Error during Gemini call: {msg}", error=True)

    def on_finish():
        util.display_message("Ripgrep results updated by Gemini.", history=True)

    generation_kwargs = util.create_generation_kwargs(
        contents=[full_prompt]
    )
    
    util.start_async_job(client, generation_kwargs, {
        'on_chunk': on_chunk,
        'on_error': on_error,
        'on_finish': on_finish,
        'status_message': "Modifying Ripgrep results..."
    }, job_id=job_id)

def apply():
    global RIPGREP_CONFIG_STORE

    rg_buffer = None
    for buf in vim.buffers:
        if buf.name and buf.name.endswith('ViminiRipGrep'):
            rg_buffer = buf
            break
    if not rg_buffer:
        util.display_message("ViminiRipGrep buffer not found.", error=True)
        return

    if not RIPGREP_CONFIG_STORE:
        util.display_message("No ripgrep session data. Please run ViminiRipGrep first.", error=True)
        return

    file_ranges = RIPGREP_CONFIG_STORE['file_ranges']
    context_separator = RIPGREP_CONFIG_STORE['context_separator']
    project_root = RIPGREP_CONFIG_STORE.get('project_root', os.getcwd())

    buffer_content = rg_buffer[:]
    changes = _parse_modified_buffer(buffer_content, file_ranges, context_separator)
    modified_files = _apply_changes(changes, file_ranges, project_root)

    for absolute_path in modified_files:
        normalized_target_path = os.path.abspath(absolute_path)
        for buf in vim.buffers:
            if buf.name and os.path.abspath(buf.name) == normalized_target_path:
                vim.command(f'checktime {buf.number}')
                break

    RIPGREP_CONFIG_STORE = {}
    vim.command(f'bdelete! {rg_buffer.number}')
    util.display_message("Changes applied.", history=True)
