import vim
import os, json, subprocess, tempfile, uuid
from google.genai import types
from vimini import util

# Global data store to exchange data between python calls without using vim variables for large data.
_VIMINI_DATA_STORE = {}

def code(prompt, verbose=False):
    """
    Uploads all open files, sends them to the Gemini API with a prompt
    to generate code. Displays thoughts (if verbose) and a combined diff
    for multiple file changes in new buffers.
    """
    util.log_info(f"code({prompt}, verbose={verbose})")
    try:
        client = util.get_client()
        if not client:
            return

        original_buffer = vim.current.buffer

        project_root = util.get_git_repo_root()
        if not project_root:
            project_root = vim.eval('getcwd()')

        # Upload context files using the helper function.
        uploaded_files = util.upload_context_files(client)
        if uploaded_files is None:
            return # The helper function has already displayed an error.

        # Define the schema for a structured JSON output with multiple files.
        file_object_schema = types.Schema(
            type=types.Type.OBJECT,
            properties={
                'file_path': types.Schema(type=types.Type.STRING, description="The full path of the file relative to the project directory."),
                'file_content': types.Schema(type=types.Type.STRING, description="The new, complete source code for the file.")
            },
            required=['file_path', 'file_content']
        )
        multi_file_output_schema = types.Schema(
            type=types.Type.OBJECT,
            properties={
                'files': types.Schema(
                    type=types.Type.ARRAY,
                    items=file_object_schema
                )
            },
            required=['files']
        )

        main_file_name = os.path.relpath(original_buffer.name, project_root) if original_buffer.name and os.path.isabs(original_buffer.name) else (original_buffer.name or f"Buffer {original_buffer.number}")
        full_prompt = [
            (
                f"{prompt}\n\n"
                "Based on the user's request, please generate the code. "
                f"Your primary task is to modify the file named '{main_file_name}'. "
                "The other files have been provided for context.\n\n"
                "IMPORTANT:\n"
                "1. Your response must be a single JSON object with a 'files' key.\n"
                "2. The value of 'files' must be an array of file objects.\n"
                "3. Each file object must have two string keys: 'file_path' and 'file_content'.\n"
                "4. 'file_path' must be the full path of the file relative to the project directory.\n"
                "5. 'file_content' must be the new, complete source code for that file.\n"
                "6. You can modify existing files or create new files as needed to fulfill the request."
            ),
            *uploaded_files
        ]

        thoughts_buffer = None
        if verbose:
            vim.command('vnew')
            vim.command('file Vimini Thoughts')
            vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
            thoughts_buffer = vim.current.buffer
            thoughts_buffer[:] = ['']

        util.display_message("Thinking...")

        generation_config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=multi_file_output_schema,
        )
        if verbose:
            generation_config.thinking_config=types.ThinkingConfig(
                include_thoughts=True
            )
        stream_kwargs = {
            'model': util._MODEL,
            'contents': full_prompt,
            'config': generation_config
        }

        response_stream = client.models.generate_content_stream(**stream_kwargs)
        json_aggregator = ""
        for chunk in response_stream:
            if not chunk.candidates or not chunk.candidates[0].content or not chunk.candidates[0].content.parts:
                continue
            for part in chunk.candidates[0].content.parts:
                if not part.text:
                    continue
                is_thought = hasattr(part, 'thought') and part.thought
                if is_thought and verbose and thoughts_buffer:
                    vim.command(f"{vim.eval(f'bufwinnr({thoughts_buffer.number})')}wincmd w")
                    new_lines = part.text.split('\n')
                    thoughts_buffer[-1] += new_lines[0]
                    if len(new_lines) > 1:
                        thoughts_buffer.append(new_lines[1:])
                    vim.command('normal! Gz-')
                elif not is_thought:
                    json_aggregator += part.text
                util.display_message("Thinking...")

        util.display_message("")

        try:
            parsed_json = json.loads(json_aggregator)
            files_to_process = parsed_json.get('files', [])
            if not isinstance(files_to_process, list):
                raise ValueError("'files' key is not a list.")
        except (json.JSONDecodeError, ValueError) as e:
            util.display_message(f"AI did not return valid JSON for files: {e}", error=True)
            vim.command('vnew')
            vim.command('file Vimini Raw Output')
            vim.command('setlocal buftype=nofile noswapfile')
            vim.current.buffer[:] = json_aggregator.split('\n')
            return

        if not files_to_process:
            util.display_message("AI returned no file changes.", history=True)
            return

        vim.command('vnew')
        vim.command('file Vimini Diff')
        vim.command('setlocal buftype=nofile filetype=diff noswapfile')
        diff_buffer = vim.current.buffer

        data_key = str(uuid.uuid4())
        _VIMINI_DATA_STORE[data_key] = {
            'files_to_apply': files_to_process,
            'project_root': project_root
        }
        vim.command(f"let b:vimini_data_key = '{data_key}'")

        combined_diff_output = []
        for file_op in files_to_process:
            relative_path = file_op['file_path']
            ai_generated_code = file_op['file_content']
            absolute_path = os.path.join(project_root, relative_path)

            original_content = ""
            file_exists = os.path.exists(absolute_path)
            if file_exists:
                try:
                    with open(absolute_path, 'r', encoding='utf-8') as f:
                        original_content = f.read()
                except Exception as e:
                    continue

            with tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8') as f_orig, \
                 tempfile.NamedTemporaryFile(mode='w+', delete=False, encoding='utf-8') as f_ai:
                f_orig.write(original_content)
                f_ai.write(ai_generated_code)
                orig_filepath = f_orig.name
                ai_filepath = f_ai.name

            try:
                # If the original file doesn't exist, diff against /dev/null
                # for a cleaner "new file" diff, as requested.
                source_path = orig_filepath if file_exists else "/dev/null"
                cmd = ['diff', '-u', source_path, ai_filepath]
                result = subprocess.run(cmd, capture_output=True, text=True, check=False)
                if result.returncode > 1:
                    continue # diff error

                diff_lines = result.stdout.strip().split('\n')
                if len(diff_lines) <= 2 and not original_content and not ai_generated_code:
                    continue # Empty diff

                combined_diff_output.append(f"diff --git a/{relative_path} b/{relative_path}")
                if not file_exists:
                    combined_diff_output.append("new file mode 100644")
                combined_diff_output.append(f"--- a/{relative_path}")
                combined_diff_output.append(f"+++ b/{relative_path}")
                combined_diff_output.extend(diff_lines[2:])

            finally:
                os.remove(orig_filepath)
                os.remove(ai_filepath)

        if not combined_diff_output:
            util.display_message("AI content is identical to the original files.", history=True)
            vim.command(f"bdelete! {diff_buffer.number}")
            return

        diff_buffer[:] = combined_diff_output
        vim.command('normal! 1G')

    except Exception as e:
        util.display_message(f"Error: General code() function exception: {e}", error=True)

def show_diff():
    """
    Shows the current git modifications in a new buffer.
    """
    util.log_info("show_diff()")
    try:
        repo_path = util.get_git_repo_root()
        if not repo_path:
            return # Error message handled by helper

        # Command to get the diff.
        # -C ensures git runs in the correct directory.
        cmd = ['git', '-C', repo_path, 'diff', '--color=never']

        # Execute the command.
        util.display_message("Running git diff...")
        result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        util.display_message("") # Clear message

        # Handle git errors (e.g., not a git repository).
        if result.returncode != 0 and not result.stdout.strip():
            error_message = result.stderr.strip()
            util.display_message(f"Git error: {error_message}", error=True)
            return

        # Handle case with no modifications.
        if not result.stdout.strip():
            util.display_message("No modifications found.", history=True)
            return

        # Display the diff in a new split window.
        vim.command('vnew')
        vim.command('file Git Diff')
        # Setting filetype to 'diff' helps with syntax highlighting
        vim.command('setlocal buftype=nofile filetype=diff noswapfile')

        # The output from git contains ANSI escape codes for color.
        # We place this raw output into the buffer.
        vim.current.buffer[:] = result.stdout.split('\n')

    except FileNotFoundError:
        util.display_message("Error: `git` command not found. Is it in your PATH?", error=True)
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)

def apply_code():
    """
    Finds the 'Vimini Diff' buffer, writes all specified file changes to
    disk, and reloads any affected open buffers.
    """
    util.log_info("apply_code()")
    diff_buffer = None
    thoughts_buffer = None
    for buf in vim.buffers:
        if buf.name and buf.name.endswith('Vimini Diff'):
            diff_buffer = buf
        elif buf.name and buf.name.endswith('Vimini Thoughts'):
            thoughts_buffer = buf

    if not diff_buffer:
        util.display_message("`Vimini Diff` buffer not found. Was :ViminiCode run?", error=True)
        return

    data_key = vim.eval(f"getbufvar({diff_buffer.number}, 'vimini_data_key', '')")
    if not data_key:
        util.display_message("Could not find data key in `Vimini Diff` buffer.", error=True)
        return

    stored_data = _VIMINI_DATA_STORE.get(data_key)
    if not stored_data:
        util.display_message("Could not find data associated with the key. It may have expired or been cleared.", error=True)
        return

    try:
        files_to_apply = stored_data['files_to_apply']
        project_root = stored_data['project_root']
        modified_files = []

        for file_op in files_to_apply:
            relative_path = file_op['file_path']
            content = file_op['file_content']
            absolute_path = os.path.join(project_root, relative_path)

            try:
                # Ensure the directory for the file exists.
                dir_name = os.path.dirname(absolute_path)
                if dir_name:
                    os.makedirs(dir_name, exist_ok=True)

                # Write the new content to the file.
                with open(absolute_path, 'w', encoding='utf-8') as f:
                    f.write(content)
                modified_files.append(relative_path)

                # Check if this file is open in a buffer and reload it.
                normalized_target_path = os.path.abspath(absolute_path)
                for buf in vim.buffers:
                    if buf.name and os.path.abspath(buf.name) == normalized_target_path:
                        # Find a window displaying this buffer to switch to.
                        win_nr = vim.eval(f'bufwinnr({buf.number})')
                        if int(win_nr) > 0:
                            vim.command(f"{win_nr}wincmd w") # Switch to window
                            vim.command('e!') # Revert to saved version
                            vim.command('wincmd p') # Switch back
                        break

            except Exception as e:
                util.display_message(f"Error writing to {relative_path}: {e}", error=True)
                # Continue to try and apply other files.

        # Clean up temporary buffers.
        vim.command(f"bdelete! {diff_buffer.number}")
        if thoughts_buffer and thoughts_buffer.number in [b.number for b in vim.buffers]:
            vim.command(f"bdelete! {thoughts_buffer.number}")

        if modified_files:
            util.display_message(f"Applied changes to: {', '.join(modified_files)}", history=True)

    except (KeyError, os.error) as e:
        util.display_message(f"Error applying changes: {e}", error=True)
    finally:
        if data_key in _VIMINI_DATA_STORE:
            del _VIMINI_DATA_STORE[data_key]
