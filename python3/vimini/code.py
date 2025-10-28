import vim
import os, json, subprocess, tempfile
from google.genai import types
from vimini import util

# Global data store to exchange data between python calls without using vim variables for large data.
_VIMINI_DATA_STORE = {}

def code(prompt, verbose=False, temperature=None):
    """
    Uploads all open files, sends them to the Gemini API with a prompt
    to generate code. Displays thoughts (if verbose) and a combined diff
    for multiple file changes in new buffers.
    """
    global _VIMINI_DATA_STORE
    util.log_info(f"code({prompt}, verbose={verbose}, temperature={temperature})")

    # --- 1. Initialization and Setup ---
    try:
        client = util.get_client()
        if not client:
            return

        project_root = util.get_git_repo_root()
        if not project_root:
            project_root = vim.eval('getcwd()')

        # Upload context files using the helper function.
        uploaded_files = util.upload_context_files(client)
        if uploaded_files is None:
            return  # The helper function has already displayed an error.
    except Exception as e:
        util.display_message(f"Error during initialization: {e}", error=True)
        return

    # --- 2. Define Schema and Prompt ---
    # This section is unlikely to raise exceptions.
    file_object_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            'file_path': types.Schema(type=types.Type.STRING, description="The full path of the file relative to the project directory."),
            'file_type': types.Schema(type=types.Type.STRING, description="The content type. Use 'text/plain' for the full file content or 'text/x-diff' for a patch in the unified diff format."),
            'file_content': types.Schema(type=types.Type.STRING, description="The new, complete source code for the file, or a patch in the unified diff format, corresponding to the file_type.")
        },
        required=['file_path', 'file_type', 'file_content']
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

    original_buffer = vim.current.buffer
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
            "3. Each file object must have three string keys: 'file_path', 'file_type', and 'file_content'.\n"
            "4. 'file_path' must be the full path of the file relative to the project directory.\n"
            "5. 'file_type' must be either 'text/plain' for the full file content or 'text/x-diff' for a patch in the unified diff format.\n"
            "6. 'file_content' must contain either the new, complete source code or the diff patch, corresponding to the 'file_type'.\n"
            "7. Diffs ('text/x-diff') can be returned only if explicitly mentioned as an acceptable output in the prompt or if the files are really difficult or too large to process. For small files, returning the entire modified file ('text/plain') is the most preferred option.\n"
            "8. You can modify existing files or create new files as needed to fulfill the request."
        ),
        *uploaded_files
    ]

    # --- 3. Set up Thoughts Buffer (if verbose) ---
    thoughts_buffer = None
    if verbose:
        try:
            util.new_split()
            vim.command('file Vimini Thoughts')
            vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
            thoughts_buffer = vim.current.buffer
            thoughts_buffer[:] = ['']
        except Exception as e:
            util.display_message(f"Error creating thoughts buffer: {e}", error=True)
            return

    # --- 4. Call API and Stream Response ---
    json_aggregator = ""
    try:
        util.display_message("Processing...")

        kwargs = util.create_generation_kwargs(
            contents=full_prompt,
            temperature=temperature,
            verbose=verbose,
            response_mime_type="application/json",
            response_schema=multi_file_output_schema
        )
        response_stream = client.models.generate_content_stream(**kwargs)
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
                util.display_message("Processing...")

        util.display_message("")
    except Exception as e:
        util.display_message("")  # Clear "Processing..."
        util.display_message(f"Error during API call to Gemini: {e}", error=True)
        return

    # --- 5. Parse JSON Response ---
    try:
        parsed_json = json.loads(json_aggregator)
        files_to_process = parsed_json.get('files', [])
        if not isinstance(files_to_process, list):
            raise ValueError("'files' key is not a list.")
    except (json.JSONDecodeError, ValueError) as e:
        util.display_message(f"AI did not return valid JSON for files: {e}", error=True)
        util.new_split()
        vim.command('file Vimini Raw Output')
        vim.command('setlocal buftype=nofile noswapfile')
        vim.current.buffer[:] = json_aggregator.split('\n')
        return

    if not files_to_process:
        util.display_message("AI returned no file changes.", history=True)
        return

    # --- 6. Generate and Display Diff ---
    try:
        util.new_split()
        vim.command('file Vimini Diff')
        vim.command('setlocal buftype=nofile filetype=diff noswapfile')
        diff_buffer = vim.current.buffer

        _VIMINI_DATA_STORE = {
            'files_to_apply': files_to_process,
            'project_root': project_root
        }
        vim.command(f"let b:vimini_project_root = '{project_root}'")

        combined_diff_output = []
        for file_op in files_to_process:
            relative_path = file_op['file_path']
            ai_generated_code = file_op['file_content']
            file_type = file_op.get('file_type', 'text/plain')
            absolute_path = os.path.join(project_root, relative_path)
            file_exists = os.path.exists(absolute_path)

            if file_type == 'text/x-diff':
                if ai_generated_code.strip():
                    combined_diff_output.extend(ai_generated_code.split('\n'))
            else: # 'text/plain' or unspecified
                original_content = ""
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
            util.display_message("AI content is identical to the original files or returned empty diff.", history=True)
            vim.command(f"bdelete! {diff_buffer.number}")
            return

        diff_buffer[:] = combined_diff_output
        vim.command('normal! 1G')
    except Exception as e:
        util.display_message(f"Error generating or displaying diff: {e}", error=True)
        return

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
        util.new_split()
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
    disk, and reloads any affected open buffers. If an error occurs, the
    diff buffer is preserved for manual editing and re-application.
    """
    global _VIMINI_DATA_STORE
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

    stored_data = _VIMINI_DATA_STORE if _VIMINI_DATA_STORE else None

    if stored_data:
        # Initial apply from AI response data.
        try:
            files_to_apply = stored_data['files_to_apply']
            project_root = stored_data['project_root']
            modified_files = []
            has_errors = False

            for file_op in files_to_apply:
                relative_path = file_op['file_path']
                content = file_op['file_content']
                file_type = file_op.get('file_type', 'text/plain')
                absolute_path = os.path.join(project_root, relative_path)

                try:
                    dir_name = os.path.dirname(absolute_path)
                    if dir_name:
                        os.makedirs(dir_name, exist_ok=True)

                    if file_type == 'text/x-diff':
                        result = subprocess.run(
                            ['patch', '-p1', '-N', '-r', '-'],
                            input=content, text=True, check=False,
                            capture_output=True, cwd=project_root
                        )
                        if result.returncode != 0:
                            err_msg = f"patch failed for {relative_path}.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
                            util.display_message(err_msg, error=True)
                            has_errors = True
                            continue
                    else:  # 'text/plain' or unspecified
                        with open(absolute_path, 'w', encoding='utf-8') as f:
                            f.write(content)

                    modified_files.append(relative_path)

                    # Reload buffer if file is open
                    normalized_target_path = os.path.abspath(absolute_path)
                    for buf in vim.buffers:
                        if buf.name and os.path.abspath(buf.name) == normalized_target_path:
                            win_nr = vim.eval(f'bufwinnr({buf.number})')
                            if int(win_nr) > 0:
                                vim.command(f"{win_nr}wincmd w")
                                vim.command('e!')
                                vim.command('wincmd p')
                            break
                except FileNotFoundError:
                    util.display_message("Error: `patch` command not found. Is it in your PATH?", error=True)
                    has_errors = True
                    break # Fatal error
                except Exception as e:
                    util.display_message(f"Error processing {relative_path}: {e}", error=True)
                    has_errors = True

            if has_errors:
                util.display_message("Errors occurred. Diff buffer is preserved for manual review and re-application.", error=True)
                return

            _VIMINI_DATA_STORE = {}

            # Success
            if modified_files:
                util.display_message(f"Applied changes to: {', '.join(modified_files)}", history=True)

            vim.command(f"bdelete! {diff_buffer.number}")
            if thoughts_buffer and thoughts_buffer.number in [b.number for b in vim.buffers]:
                vim.command(f"bdelete! {thoughts_buffer.number}")

        except Exception as e:
            util.display_message(f"An unexpected error occurred during apply: {e}", error=True)
            _VIMINI_DATA_STORE = {}
        return

    # Re-apply from buffer content.
    project_root = vim.eval(f"getbufvar({diff_buffer.number}, 'vimini_project_root', '')")
    if not project_root:
        project_root = util.get_git_repo_root() or vim.eval('getcwd()')

    diff_content = "\n".join(diff_buffer[:])
    if not diff_content.strip():
        util.display_message("Diff is empty. Nothing to apply.", history=True)
        vim.command(f"bdelete! {diff_buffer.number}")
        return

    try:
        result = subprocess.run(
            ['patch', '-p1', '-N', '-r', '-'],
            input=diff_content, text=True, check=False,
            capture_output=True, cwd=project_root
        )

        if result.returncode != 0:
            err_msg = f"Patch command failed. Please review the output and the diff.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
            util.display_message(err_msg, error=True)
            return  # Preserve buffer

        # Success on re-apply
        util.display_message("Successfully applied modified diff.", history=True)

        modified_files = []
        for line in diff_content.split('\n'):
            if line.startswith('--- a/'):
                path = line[len('--- a/'):].strip()
                if path != '/dev/null':
                    modified_files.append(path)

        for relative_path in set(modified_files):
            absolute_path = os.path.join(project_root, relative_path)
            normalized_target_path = os.path.abspath(absolute_path)
            for buf in vim.buffers:
                if buf.name and os.path.abspath(buf.name) == normalized_target_path:
                    win_nr = vim.eval(f'bufwinnr({buf.number})')
                    if int(win_nr) > 0:
                        vim.command(f"{win_nr}wincmd w")
                        vim.command('e!')
                        vim.command('wincmd p')
                    break

        vim.command(f"bdelete! {diff_buffer.number}")
        if thoughts_buffer and thoughts_buffer.number in [b.number for b in vim.buffers]:
            vim.command(f"bdelete! {thoughts_buffer.number}")

    except FileNotFoundError:
        util.display_message("Error: `patch` command not found. Is it in your PATH?", error=True)
    except Exception as e:
        util.display_message(f"Error applying modified diff: {e}", error=True)