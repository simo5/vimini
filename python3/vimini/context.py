import vim
import os
import io
import time
import json
from . import util
from google.genai import types

# --- Context File Uploading ---
# (moved from util.py)

def find_context_files(file_paths_to_include=None):
    """
    Generate a list of files to be used as context.
    If file_paths_to_include is provided, it will be used as the source of truth for file paths.
    Otherwise, this includes:
    1. All files currently open in Vim buffers that are backed by a file on disk.
    2. All files specified in `g:context_files`, which are always included if they exist.

    If a file is both in an open buffer and `g:context_files`, the content
    from the open buffer is used. Each element of the list is a tuple containing
    the file path and the corresponding vim buffer number (or None if the file
    is not open in a buffer and should be read from disk).
    """
    files_to_upload = []
    seen_file_paths = set()

    if file_paths_to_include is not None:
        # If a specific list of files is provided, use that.
        # We still need to check if they are open in buffers to get latest content.
        open_buffers_by_path = {}
        for b in vim.buffers:
            if b.name and os.path.exists(b.name):
                open_buffers_by_path[os.path.abspath(b.name)] = b.number

        for file_path in file_paths_to_include:
            abs_path = os.path.abspath(file_path)
            if os.path.exists(abs_path) and abs_path not in seen_file_paths:
                buf_num = open_buffers_by_path.get(abs_path)
                files_to_upload.append((abs_path, buf_num))
                seen_file_paths.add(abs_path)
        return files_to_upload

    # First, add all file-backed buffers. This gives them priority.
    for b in vim.buffers:
        if b.name and os.path.exists(b.name):
            abs_path = os.path.abspath(b.name)
            if abs_path not in seen_file_paths:
                files_to_upload.append((abs_path, b.number))
                seen_file_paths.add(abs_path)

    # Second, add any files from g:context_files that aren't already in the list.
    context_files_list = []
    try:
        var = vim.eval("get(g:, 'context_files', [])")
        if isinstance(var, list):
            context_files_list = var
    except (vim.error, AttributeError):
        pass # Not in vim or variable doesn't exist.

    for file_path in context_files_list:
        abs_path = os.path.abspath(file_path)
        if os.path.exists(abs_path) and abs_path not in seen_file_paths:
            # This file should be read from disk.
            files_to_upload.append((abs_path, None))
            seen_file_paths.add(abs_path)

    return files_to_upload

def upload_context_files(client, file_paths_to_include=None):
    """
    Uploads files to use as context. This includes files from open buffers and
    from the `g:context_files` list. It re-uploads files if they have been
    modified since the last upload.
    Returns a list of active file API resources, or None on failure.
    """
    # --- 1. Determine which files to upload vs. reuse ---
    files_to_process = []
    files_requiring_upload = []
    util.display_message("Checking context files...")

    context_files = find_context_files(file_paths_to_include)
    if context_files:
        util.log_info(f"Considering these context files {context_files}")
    else:
        util.display_message("No context files found (from open buffers or g:context_files).", history=True)
        return None

    # Load existing files into a map for quick lookup.
    existing_files = {}
    try:
        for f in client.files.list():
            existing_files[f.display_name] = f
    except Exception:
        # Ignore errors; if listing fails, we'll just upload everything.
        pass

    for file_path, buf_number in context_files:
        found_file = existing_files.get(file_path)

        if not found_file:
            # Not found, needs uploading.
            files_requiring_upload.append((file_path, buf_number))
            continue

        # File was found, check if it's stale.
        is_stale = False
        buffer_is_modified = False
        if buf_number is not None:
            buffer = vim.buffers[buf_number]
            if util.is_buffer_modified(buffer):
                is_stale = True
                buffer_is_modified = True

        if not buffer_is_modified:
            # Check disk if buffer isn't modified, or if there is no buffer.
            try:
                disk_mtime = os.path.getmtime(file_path)
                uploaded_time = found_file.create_time.timestamp()
                if uploaded_time < disk_mtime:
                    is_stale = True
            except (OSError, AttributeError):
                is_stale = True # Re-upload to be safe.

        if is_stale:
            files_requiring_upload.append((file_path, buf_number))
            # It's good practice to delete the old one.
            try:
                client.files.delete(name=found_file.name)
            except Exception:
                pass # Deletion is best-effort.
        else:
            # Uploaded file is recent enough, use it.
            files_to_process.append(found_file)

    # --- Log context files status before upload ---
    reused_file_paths = {f.display_name for f in files_to_process}
    upload_file_paths = {path for path, _ in files_requiring_upload}
    all_context_file_paths = sorted(list(reused_file_paths | upload_file_paths))

    util.log_info(f"Found {len(all_context_file_paths)} context files:")
    for file_path in all_context_file_paths:
        status = " (will upload)" if file_path in upload_file_paths else " (already available)"
        util.log_info(f"  - {file_path}{status}")


    # --- 2. Prepare and filter files for upload ---
    files_with_content = []
    for file_path, buf_number in files_requiring_upload:
        content = ""
        if buf_number is not None:
            buf = vim.buffers[buf_number]
            content = "\n".join(buf[:])
        else:
            # Read from disk for files not in a buffer.
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except Exception as e:
                util.display_message(f"Could not read context file {file_path}: {e}", error=True)
                continue # Skip this file

        if not content.strip():
            continue

        content_bytes = content.encode('utf-8')
        files_with_content.append({
            'path': file_path,
            'content_bytes': content_bytes,
            'size': len(content_bytes)
        })

    # Limit total upload size to 1MB
    MAX_UPLOAD_BYTES = 1 * 1024 * 1024 # 1 Megabyte
    total_size = sum(f['size'] for f in files_with_content)
    eliminated_files = []

    while total_size > MAX_UPLOAD_BYTES and files_with_content:
        # Find and remove the largest file
        largest_file = max(files_with_content, key=lambda f: f['size'])
        files_with_content.remove(largest_file)
        total_size -= largest_file['size']
        eliminated_files.append(os.path.basename(largest_file['path']))

    if eliminated_files:
        util.display_message(f"Context files > 1MB. Excluded: {', '.join(sorted(eliminated_files))}", history=True)
        util.log_info(f"Excluded {len(eliminated_files)} files from context upload due to size limit: {', '.join(sorted(eliminated_files))}")


    # --- 3. Upload necessary files ---
    if files_with_content:
        util.display_message(f"Uploading {len(files_with_content)} context file(s)...")

    uploaded_files = []
    for file_info in files_with_content:
        file_path = file_info['path']
        buf_content_bytes = file_info['content_bytes']

        buf_io = io.BytesIO(buf_content_bytes)
        # Always force plain text, the GEeminiAPI is very fussy with thr type
        # and returns 400 errors on types it doesn't like
        mime_type = 'text/plain'

        try:
            uploaded_file = client.files.upload(
                file=buf_io,
                config=types.UploadFileConfig(
                    display_name=file_path,
                    mime_type=mime_type
                ),
            )
            uploaded_files.append(uploaded_file)
        except Exception as e:
            util.display_message(f"Error uploading {file_path}: {e}", error=True)
            return None # Fail fast on upload error

    # --- 4. Wait for all files (reused and new) to become ACTIVE ---
    pending_files = []
    for f in uploaded_files:
        # Re-check the state of reused files as well.
        if f.state.name == 'ACTIVE':
            files_to_process.append(f)
        elif f.state.name == 'PROCESSING':
            pending_files.append(f)
        else: # FAILED, etc.
             util.display_message(f"Reused file {f.display_name} is in an unusable state: {f.state.name}", error=True)
             return None

    start_time = time.time()
    timeout = 2.0
    while pending_files:
        if time.time() - start_time > timeout:
            util.display_message(f"File processing timed out after {int(timeout)}s.", error=True)
            return None
        remaining_time = timeout - (time.time() - start_time)
        util.display_message(f"Waiting for {len(pending_files)} files... ({remaining_time:.1f}s left)")
        time.sleep(0.1)
        still_pending = []
        for f in pending_files:
            try:
                updated_file = client.files.get(name=f.name)
                if updated_file.state.name == 'PROCESSING':
                    still_pending.append(updated_file)
                elif updated_file.state.name == 'ACTIVE':
                    files_to_process.append(updated_file)
                else: # FAILED or other terminal state
                    util.display_message(f"File processing failed for {updated_file.display_name}: {updated_file.state.name}", error=True)
                    return None
            except Exception as e:
                util.display_message(f"Error checking file status for {f.display_name}: {e}", error=True)
                return None
        pending_files = still_pending

    if not files_to_process:
        util.display_message("No content found in open buffers to create context.", history=True)
        return None

    util.log_info(f"Final active context files ({len(files_to_process)}):")
    for file in files_to_process:
        util.log_info(f"  - {file.display_name}")

    return files_to_process


# --- Interactive Context File Manager ---
# (moved from main.py)

# Global variable to hold the list of pending context files while the
# context file manager is open.
_VIMINI_PENDING_CONTEXT_FILES = None

def _draw_context_files_listing(target_path, project_root, context_files_list):
    """
    Generates the list of lines for the context files buffer.
    """
    # 1. Create a set of absolute paths for files in context for quick lookups.
    context_files_abs = set()
    for f in context_files_list:
        path = os.path.expanduser(f)
        if not os.path.isabs(path):
            path = os.path.join(project_root, path)
        context_files_abs.add(os.path.normpath(path))

    # 2. Prepare buffer header
    buffer_lines = [
        "| Vimini Context Files",
        "|------------------------------------------",
        "| <CR>: toggle/enter | l: list | q: close",
        "| C: in context | >: directory",
        "",
        "> .."
    ]

    # 3. Get directory listing
    dirs_to_ignore = {'.git', '__pycache__', 'node_modules', '.venv', 'target'}
    dirs, files = [], []
    try:
        for name in os.listdir(target_path):
            if name in dirs_to_ignore:
                continue
            full_path = os.path.join(target_path, name)
            if os.path.isdir(full_path):
                dirs.append(name)
            else:
                files.append(name)
    except OSError as e:
        util.display_message(f"Error reading directory '{target_path}': {e}", error=True)
        return None

    # 4. Format and append directory and file lines
    for d in sorted(dirs):
        buffer_lines.append(f"> {d}")
    for f in sorted(files):
        full_path = os.path.join(target_path, f)
        is_in_context = os.path.normpath(full_path) in context_files_abs
        prefix = "C " if is_in_context else "  "
        buffer_lines.append(f"{prefix}{f}")

    return buffer_lines

def context_files_command():
    """
    Shows a new buffer with a file explorer to manage g:context_files.
    """
    util.log_info("context_files_command()")
    global _VIMINI_PENDING_CONTEXT_FILES
    try:
        # 1. Get paths and existing context files
        current_path = os.path.abspath(vim.eval('getcwd()'))
        project_root = util.get_git_repo_root() or current_path

        try:
            initial_context_files = vim.eval("get(g:, 'context_files', [])")
            if not isinstance(initial_context_files, list):
                initial_context_files = []
        except vim.error:
            initial_context_files = []

        # Store the pending list in the global python variable.
        _VIMINI_PENDING_CONTEXT_FILES = initial_context_files

        # 2. Get directory listing using the helper
        buffer_lines = _draw_context_files_listing(current_path, project_root, _VIMINI_PENDING_CONTEXT_FILES)
        if buffer_lines is None:
            return # Error was displayed by helper

        # 3. Create and populate the new buffer
        util.new_split()
        vim.command('file ViminiContextFiles')
        buf = vim.current.buffer
        buf[:] = buffer_lines

        # 4. Set buffer options
        vim.command('setlocal buftype=nofile noswapfile nomodifiable')
        vim.command(f"let b:vimini_context_root = '{project_root}'")
        vim.command(f"let b:vimini_context_path = '{current_path}'")

        # 5. Set up key mappings and autocmd for close confirmation
        vim.command("nnoremap <buffer> <silent> <CR> :py3 from vimini.context import toggle_context_file; toggle_context_file()<CR>")
        vim.command("nnoremap <buffer> <silent> l :py3 from vimini.context import show_context_lists; show_context_lists()<CR>")
        vim.command("nnoremap <buffer> <silent> q :q<CR>")
        vim.command("autocmd BufUnload <buffer> :py3 from vimini.context import confirm_context_files; confirm_context_files()")
        # Move cursor past header to the first file/directory entry.
        vim.current.window.cursor = (6, 0)
        vim.command('setlocal readonly')

    except Exception as e:
        util.display_message(f"Error managing context files: {e}", error=True)

def toggle_context_file():
    """
    Called by <Enter> mapping in the ViminiContextFiles buffer.
    Adds/removes a file from g:context_files, or navigates directories.
    """
    global _VIMINI_PENDING_CONTEXT_FILES
    try:
        buf = vim.current.buffer
        win = vim.current.window
        line_num, col = win.cursor
        line = buf[line_num - 1]

        # Ignore empty lines or header/comment lines
        if not line.strip() or line.strip().startswith('|'):
            return

        current_path = vim.eval("get(b:, 'vimini_context_path', '')")
        project_root = vim.eval("get(b:, 'vimini_context_root', '')")
        if not current_path or not project_root:
            util.display_message("Error: Context buffer variables not set.", error=True)
            return

        # --- Directory Navigation Logic ---
        if line.startswith('> '):
            dir_name = line[2:].strip()
            if dir_name == '..':
                new_path = os.path.dirname(current_path)
            else:
                new_path = os.path.join(current_path, dir_name)

            # If not a valid directory, stay in the current one to redraw it.
            if not os.path.isdir(new_path):
                new_path = current_path

            new_path = os.path.abspath(new_path)

            # Re-render the buffer for the new path
            context_files_list = _VIMINI_PENDING_CONTEXT_FILES
            if not isinstance(context_files_list, list):
                # This should not happen if context_files_command() was called.
                util.display_message("Error: Pending context files list is not available.", error=True)
                return

            buffer_lines = _draw_context_files_listing(new_path, project_root, context_files_list)
            if buffer_lines is None:
                return # Error displayed by helper

            vim.command('setlocal modifiable')
            buf[:] = buffer_lines
            vim.command(f"let b:vimini_context_path = '{new_path}'")
            vim.command('setlocal readonly')
            win.cursor = (6, 0)
            return

        # --- File Toggling Logic ---
        if len(line) < 3: return
        prefix = line[:2]
        file_name = line[2:].strip()

        full_path_on_line = os.path.normpath(os.path.join(current_path, file_name))

        # If not a valid file, redraw current directory and do nothing else.
        if not os.path.isfile(full_path_on_line):
            context_files_list = _VIMINI_PENDING_CONTEXT_FILES
            if not isinstance(context_files_list, list):
                util.display_message("Error: Pending context files list is not available.", error=True)
                return

            buffer_lines = _draw_context_files_listing(current_path, project_root, context_files_list)
            if buffer_lines is None:
                return # Error displayed by helper

            vim.command('setlocal modifiable')
            buf[:] = buffer_lines
            vim.command('setlocal readonly')
            win.cursor = (6, 0) # cursor to top after redraw
            return

        is_in_context = (prefix == "C ")

        relative_path_for_storage = os.path.relpath(full_path_on_line, project_root)

        context_files_list = _VIMINI_PENDING_CONTEXT_FILES
        if not isinstance(context_files_list, list):
            util.display_message("Error: Pending context files list is not available.", error=True)
            return

        new_list = []
        if is_in_context:
            # Remove it
            for f in context_files_list:
                path_in_list = os.path.expanduser(f)
                if not os.path.isabs(path_in_list):
                    path_in_list = os.path.join(project_root, path_in_list)
                if os.path.normpath(path_in_list) != full_path_on_line:
                    new_list.append(f)
            new_prefix = "  "
        else:
            # Add it
            new_list = context_files_list
            is_already_present = False
            for f in context_files_list:
                path_in_list = os.path.expanduser(f)
                if not os.path.isabs(path_in_list):
                    path_in_list = os.path.join(project_root, path_in_list)
                if os.path.normpath(path_in_list) == full_path_on_line:
                    is_already_present = True
                    break
            if not is_already_present:
                new_list.append(relative_path_for_storage)
            new_prefix = "C "

        _VIMINI_PENDING_CONTEXT_FILES = new_list

        vim.command('setlocal modifiable')
        buf[line_num - 1] = f"{new_prefix}{file_name}"
        vim.command('setlocal readonly')
        vim.command("redraw")
        win.cursor = (line_num, col)

    except Exception as e:
        vim.command(f"echoerr '[Vimini] Error toggling context file: {str(e).replace("'", "''")}'")

def show_context_lists():
    """
    Called by 'l' mapping in the ViminiContextFiles buffer.
    Shows a popup with the current and pending context file lists.
    """
    global _VIMINI_PENDING_CONTEXT_FILES
    popup_id = 0
    try:
        # Get active context files
        try:
            active_files = vim.eval("get(g:, 'context_files', [])")
            if not isinstance(active_files, list):
                active_files = []
        except vim.error:
            active_files = []

        # Get pending context files
        pending_files = _VIMINI_PENDING_CONTEXT_FILES
        if pending_files is None:
            # This shouldn't happen if the buffer is open, but as a safeguard
            pending_files = active_files

        # Build popup content
        popup_content = ["--- Active Context Files ---"]
        if active_files:
            popup_content.extend(sorted(active_files))
        else:
            popup_content.append("(none)")

        # Compare and add pending files if different
        if sorted(active_files) != sorted(pending_files):
            popup_content.append("")
            popup_content.append("--- Pending Context Files (unsaved) ---")
            if pending_files:
                popup_content.extend(sorted(pending_files))
            else:
                popup_content.append("(none)")

        popup_content.extend(['', '(Press any key to close)'])

        # Create the popup
        popup_options = {
            'title': ' Context Lists ', 'line': 0, 'col': 0,
            'minwidth': 40, 'maxwidth': 80,
            'padding': [1, 2, 1, 2], 'border': [1, 1, 1, 1],
            'borderchars': ['─', '│', '─', '│', '╭', '╮', '╯', '╰'],
            'close': 'none', 'zindex': 200,
        }
        popup_id = vim.eval(f"popup_create({json.dumps(popup_content)}, {popup_options})")
        vim.command("redraw!")
        # Wait for any key to be pressed.
        vim.eval('getchar()')

    except Exception as e:
        util.display_message(f"Error showing context lists: {e}", error=True)
    finally:
        # Ensure the popup is always closed.
        if int(popup_id) > 0:
            vim.eval(f"popup_close({popup_id})")
            vim.command("redraw!")

def confirm_context_files():
    """
    Called on BufUnload of the context files buffer.
    Shows a confirmation popup to save or discard changes to the context.
    """
    global _VIMINI_PENDING_CONTEXT_FILES
    try:
        # If the global variable was not set, something is wrong, or another
        # buffer closed. We should only act if it's populated.
        if _VIMINI_PENDING_CONTEXT_FILES is None:
            return

        pending_files = _VIMINI_PENDING_CONTEXT_FILES

        # Get the original list
        try:
            original_files = vim.eval("get(g:, 'context_files', [])")
            if not isinstance(original_files, list):
                original_files = []
        except vim.error:
            original_files = []

        # If there's no change, do nothing.
        if sorted(pending_files) == sorted(original_files):
            return

        # Build the popup content
        popup_content = ["Set new context files?", ""]
        if pending_files:
            popup_content.append("--- Files ---")
            for f in sorted(pending_files):
                popup_content.append(f"- {f}")
        else:
            popup_content.append("(Context will be empty)")

        popup_content.extend(['', '---', 'Accept changes? [y/n]'])

        popup_options = {
            'title': ' Confirm Context ', 'line': 0, 'col': 0,
            'minwidth': 40, 'maxwidth': 80,
            'padding': [1, 2, 1, 2], 'border': [1, 1, 1, 1],
            'borderchars': ['─', '│', '─', '│', '╭', '╮', '╯', '╰'],
            'close': 'none', 'zindex': 200,
        }
        popup_id = vim.eval(f"popup_create({popup_content}, {popup_options})")
        vim.command("redraw!")

        commit_confirmed = False
        try:
            answer_code = vim.eval('getchar()')
            answer_char = chr(int(answer_code))
            if answer_char.lower() == 'y':
                commit_confirmed = True
        except (vim.error, ValueError, TypeError):
            pass
        finally:
            vim.eval(f"popup_close({popup_id})")
            vim.command("redraw!")

        if commit_confirmed:
            # Use json.dumps to create a string that is a valid Vimscript list literal.
            vim.command(f"let g:context_files = {json.dumps(pending_files)}")
            util.display_message("Context files updated.", history=True)
        else:
            util.display_message("Context file changes discarded.", history=True)

    except Exception as e:
        util.display_message(f"Error confirming context files: {e}", error=True)
    finally:
        # Clean up the global variable now that the context manager is closed.
        _VIMINI_PENDING_CONTEXT_FILES = None


# --- Remote File Manager ---

def _refresh_files_buffer():
    """
    Helper to re-fetch files and update the content of the 'Vimini Files' buffer.
    """
    # Find the 'Vimini Files' buffer
    vimini_files_buffer = None
    for b in vim.buffers:
        if b.valid and b.name and b.name.endswith('Vimini Files'):
            vimini_files_buffer = b
            break
    if not vimini_files_buffer:
        return

    client = util.get_client()
    if not client:
        return

    all_files = list(client.files.list())
    file_list_content = [
        "Vimini Remote Files",
        "-------------------",
        " d: delete | D: delete all | i: info | q: close",
        ""
    ]
    if not all_files:
        file_list_content.append("No files have been uploaded.")
    else:
        # Sorting is good for consistency
        for f in sorted(all_files, key=lambda x: x.display_name):
            file_list_content.append(f.display_name)

    # Switch to window, update buffer, switch back
    win_nr = int(vim.eval(f"bufwinnr({vimini_files_buffer.number})"))
    if win_nr > 0:
        original_win_nr = int(vim.eval("winnr()"))
        vim.command(f"{win_nr}wincmd w")
        # Save cursor position before modifying the buffer
        cursor_pos = vim.eval("getpos('.')")

        vim.command("setlocal modifiable")
        vimini_files_buffer[:] = file_list_content
        vim.command("setlocal nomodifiable")

        # Restore cursor position, adjusting if necessary
        new_line_count = len(vimini_files_buffer)
        lnum = int(cursor_pos[1])
        if lnum > new_line_count:
            lnum = new_line_count
        # Ensure line number is at least 1
        if lnum < 1:
            lnum = 1
        cursor_pos[1] = str(lnum)
        cursor_pos[0] = '0' # Use current buffer to be safe

        vim.command(f"call setpos('.', {cursor_pos})")

        if original_win_nr != win_nr:
            vim.command(f"{original_win_nr}wincmd w")

def _files_buffer_action(action):
    """
    Performs an action ('info' or 'delete') on the file under the cursor
    in the 'Vimini Files' buffer.
    """
    try:
        w = vim.current.window
        # Check if we are in the right buffer
        if not (w.valid and w.buffer.name and w.buffer.name.endswith('Vimini Files')):
            return

        line_num = w.cursor[0]
        line = w.buffer[line_num - 1].strip()

        # Ignore header/blank lines
        if not line or line.startswith("Vimini") or line.startswith("---") or "delete |" in line:
            return

        file_name = line
        client = util.get_client()
        if not client:
            return

        # Find the file object by its display_name
        util.display_message(f"Finding '{file_name}'...")
        target_file = None
        all_files = list(client.files.list())
        for f in all_files:
            if f.display_name == file_name:
                target_file = f
                break

        if not target_file:
            util.display_message(f"Error: File '{file_name}' no longer exists on server. Refreshing list.", error=True)
            _refresh_files_buffer()
            return

        util.display_message("") # Clear message

        if action == "info":
            info_content = [
                f"File Info: {target_file.display_name}",
                "---------------------------------",
                f"ID:           {target_file.name}",
                f"Display Name: {target_file.display_name}",
                f"MIME Type:    {target_file.mime_type}",
                f"Size:         {target_file.size_bytes} bytes",
                f"Created:      {target_file.create_time.isoformat()}",
                f"URI:          {target_file.uri}",
            ]
            util.new_split()
            vim.command(f'file Vimini File Info: {file_name}')
            vim.current.buffer[:] = info_content
            vim.command('setlocal buftype=nofile filetype=markdown noswapfile nomodifiable')

        elif action == "delete":
            util.display_message(f"Deleting '{file_name}'...")
            client.files.delete(name=target_file.name)
            util.display_message(f"File '{file_name}' deleted. Refreshing list...", history=True)
            _refresh_files_buffer()

    except Exception as e:
        util.display_message(f"Error during file action: {e}", error=True)

def _delete_all_files():
    """
    Deletes all remote files after confirmation.
    """
    try:
        client = util.get_client()
        if not client:
            return

        all_files = list(client.files.list())
        if not all_files:
            util.display_message("No remote files to delete.", history=True)
            return

        # --- Confirmation Popup ---
        popup_content = [
            f"Delete all {len(all_files)} remote files?",
            "This action cannot be undone.",
            "",
            "Confirm deletion? [y/n]"
        ]
        popup_options = {
            'title': ' Confirm Deletion ', 'line': 0, 'col': 0,
            'minwidth': 40, 'maxwidth': 60,
            'padding': [1, 2, 1, 2], 'border': [1, 1, 1, 1],
            'borderchars': ['─', '│', '─', '│', '╭', '╮', '╯', '╰'],
            'close': 'none', 'zindex': 200,
        }
        popup_id = vim.eval(f"popup_create({json.dumps(popup_content)}, {popup_options})")
        vim.command("redraw!")

        confirmed = False
        try:
            answer_code = vim.eval('getchar()')
            answer_char = chr(int(answer_code))
            if answer_char.lower() == 'y':
                confirmed = True
        except (vim.error, ValueError, TypeError):
            pass # confirmed remains False
        finally:
            vim.eval(f"popup_close({popup_id})")
            vim.command("redraw!")

        if not confirmed:
            util.display_message("Deletion of all files cancelled.", history=True)
            return

        util.display_message(f"Deleting all {len(all_files)} remote files...")
        deleted_count = 0
        failed_count = 0
        for f in all_files:
            try:
                client.files.delete(name=f.name)
                deleted_count += 1
            except Exception as e:
                util.log_info(f"Failed to delete file {f.display_name}: {e}")
                failed_count += 1

        message = f"Deleted {deleted_count} files."
        if failed_count > 0:
            message += f" {failed_count} files failed to delete."
        util.display_message(message, history=True)

        _refresh_files_buffer()

    except Exception as e:
        util.display_message(f"Error deleting all files: {e}", error=True)

def files_command():
    """
    Opens an interactive buffer listing all remote files, with key mappings
    to manage them.
    """
    util.log_info("files_command()")
    try:
        client = util.get_client()
        if not client:
            return

        util.display_message("Fetching file list...")
        all_files = list(client.files.list())
        util.display_message("")

        file_list_content = [
            "Vimini Remote Files",
            "-------------------",
            " d: delete | D: delete all | i: info | q: close",
            ""
        ]
        if not all_files:
            file_list_content.append("No files have been uploaded.")
        else:
            for f in sorted(all_files, key=lambda x: x.display_name):
                file_list_content.append(f.display_name)

        util.new_split()
        vim.command('file Vimini Files')
        buf = vim.current.buffer
        buf[:] = file_list_content
        vim.command('setlocal buftype=nofile noswapfile filetype=markdown')

        # Mappings for actions
        vim.command("nnoremap <buffer> <silent> i :py3 from vimini.context import _files_buffer_action; _files_buffer_action('info')<CR>")
        vim.command("nnoremap <buffer> <silent> d :py3 from vimini.context import _files_buffer_action; _files_buffer_action('delete')<CR>")
        vim.command("nnoremap <buffer> <silent> D :py3 from vimini.context import _delete_all_files; _delete_all_files()<CR>")
        vim.command("nnoremap <buffer> <silent> q :q<CR>")

        vim.command('setlocal nomodifiable')

    except Exception as e:
        util.display_message(f"Error listing files: {e}", error=True)
