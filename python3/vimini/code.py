import vim
import os, subprocess, tempfile, re
from vimini import util, context

# Global data store keyed by buffer number to exchange data between python calls.
_BUFFER_DATA_STORE = {}
# Separator line to distinguish between thoughts/summary and the actual diff
_DIFF_SEPARATOR = "========== VIMINI DIFF START =========="
_STREAM_JSON_STORE = {}

def _to_str(val):
    if isinstance(val, bytes):
        return val.decode('utf-8', errors='replace')
    return str(val) if val is not None else ""

def _find_buffer(req_id):
    try:
        for buf in vim.buffers:
            bid = buf.vars.get("vimini_job_id")
            if bid is not None and _to_str(bid) == str(req_id):
                return buf
    except Exception:
        pass
    return None

def handle_channel_response(req_id, result):
    """
    Handles channel responses from the agent server for code requests.
    Statuses: 'thought', 'chunk', 'completed', 'error'
    """
    if not isinstance(result, dict):
        return

    status = result.get("status")
    buf = _find_buffer(req_id)
    if buf is None:
        return
    buf_num = buf.number

    errs = result.get("processing_errors")
    if errs:
        util.append_to_buffer("**Processing Errors**\n" + "\n".join(errs))

    if status == "thought":
        if "[->G]" in buf.name:
            buf.name = buf.name.replace("[->G]", "[<-G]")
        thought_text = result.get("thought", "")
        verbose = result.get("verbose")
        if verbose is None:
            try:
                verbose = (vim.eval("get(g:, 'vimini_thinking', 'on')") == 'on')
            except Exception:
                verbose = True
        if verbose and thought_text and buf_num:
            util.append_to_buffer(buf_num, thought_text)

    elif status == "chunk":
        spin_map = {
            "[->G]": "[<-G]",
            "[<-G]": "[<-\\]",
            "[<-\\]": "[<-|]",
            "[<-|]": "[<-/]",
            "[<-/]": "[<-G]",
        }
        for spin in spin_map.items():
            if spin[0] in buf.name:
                buf.name = buf.name.replace(spin[0], spin[1])
                break

        chunk_text = result.get("text", "")
        if req_id is not None:
            _STREAM_JSON_STORE[req_id] = _STREAM_JSON_STORE.get(req_id, "") + chunk_text

    elif status == "completed":
        json_text = _STREAM_JSON_STORE.pop(req_id, "")
        files_to_process = result.get("files", [])
        diff_output = result.get("diff_output", "")
        project_root = result.get("project_root") or util.get_git_repo_root() or os.getcwd()

        if buf_num:
            _BUFFER_DATA_STORE[buf_num] = {
                "files_to_apply": files_to_process,
                "project_root": project_root,
                "req_id": req_id
            }

            if diff_output:
                separator_block = f"\n{_DIFF_SEPARATOR}\n"
                util.append_to_buffer(buf_num, separator_block + diff_output)
            else:
                util.append_to_buffer(buf_num, "\nAI content is identical to the original files or returned empty diff.")

            vim.command(f"call setbufvar({buf_num}, '&filetype', 'diff')")

            if buf:
                base_buffer_name = f"[{req_id}] Vimini Code"
                try:
                    buf.name = base_buffer_name
                except Exception:
                    pass

    elif status == "error":
        _STREAM_JSON_STORE.pop(req_id, None)
        err_msg = result.get("error", "Unknown error")
        if buf_num:
            util.append_to_buffer(buf_num, f"\nError: {err_msg}")
        util.display_message(f"Error: {err_msg}", error=True)

def code(prompt, verbose=False, temperature=None):
    """
    Uploads all open files, sends them to the Gemini API with a prompt
    to generate code via the agent server.
    """
    util.log_info(f"code({prompt}, verbose={verbose}, temperature={temperature})")

    project_root = util.get_git_repo_root()
    if not project_root:
        project_root = os.getcwd()

    file_paths_to_include = []
    for b in vim.buffers:
        if b.name and os.path.exists(b.name):
            file_paths_to_include.append(os.path.realpath(b.name))

    try:
        context_files_list = vim.eval("get(g:, 'context_files', [])")
        if isinstance(context_files_list, list):
            for f in context_files_list:
                if os.path.isabs(f):
                    file_paths_to_include.append(os.path.realpath(f))
                else:
                    file_paths_to_include.append(os.path.realpath(os.path.join(project_root, f)))
    except Exception:
        pass

    original_buffer = vim.current.buffer

    is_real_file = False
    if original_buffer.name and os.path.exists(original_buffer.name) and os.path.isfile(original_buffer.name):
        try:
            buftype = original_buffer.options['buftype'] if 'buftype' in original_buffer.options else ''
            if not buftype:
                is_real_file = True
        except Exception:
            is_real_file = True

    if is_real_file:
        main_file_name = os.path.relpath(original_buffer.name, project_root) if os.path.isabs(original_buffer.name) else original_buffer.name
        task_instruction = f"Your primary task is to modify the file named '{main_file_name}'."
    else:
        task_instruction = "Your primary task is to address the concern in the active buffer (if any).\n"
        buffer_content = "\n".join(original_buffer[:])
        if buffer_content.strip():
            task_instruction += f"\n\nAdditional context from the current active buffer:\n{buffer_content}\n"

    context_file_names = sorted([os.path.relpath(f, project_root) for f in file_paths_to_include])

    job_name = f"Code: {prompt}"
    job_id = str(util.reserve_next_job_id(job_name))

    util.new_split()
    base_buffer_name = f"[{job_id}] Vimini Code"
    safe_name = f"{base_buffer_name} [->G]".replace(" ", "\\ ")
    vim.command(f"file {safe_name}")
    vim.command("setlocal buftype=nofile")
    vim.command("setlocal bufhidden=wipe")
    vim.command("setlocal noswapfile")
    vim.command("setlocal filetype=markdown")

    code_buffer = vim.current.buffer
    code_buffer_num = code_buffer.number

    code_buffer.vars["vimini_project_root"] = project_root
    code_buffer.vars["vimini_job_id"] = job_id

    util.append_job_summary(code_buffer_num, job_id, prompt, context_file_names)

    util.display_message("Processing via agent... (Async)")

    try:
        buffers = context.get_buffer_contents(file_paths_to_include)
    except Exception as e:
        util.log_info(f"Error getting buffer contents for code prompt: {e}")
        buffers = []

    req = {
        "jsonrpc": "2.0",
        "id": str(job_id),
        "method": "code",
        "params": {
            "prompt": prompt,
            "verbose": verbose,
            "temperature": temperature,
            "project_root": project_root,
            "file_paths_to_include": file_paths_to_include,
            "buffers": buffers,
            "task_instruction": task_instruction,
        }
    }

    util.send_channel_request(req)

def _process_x_diff_chunks(ai_generated_code, relative_path, file_exists):
    """
    Helper function to parse x-diff chunks, fix the --- and +++ paths,
    and recount/adjust the line counters in @@ headers if they are incorrect.
    """
    lines = ai_generated_code.strip('\n').split('\n')
    if not lines or (len(lines) == 1 and not lines[0]):
        return []

    if not lines[0].startswith("diff"):
        lines.insert(0, f"diff --git a/{relative_path} b/{relative_path}")

    fixed_lines = []
    current_hunk_header = None
    current_hunk_data = []

    def flush_hunk():
        nonlocal current_hunk_header, current_hunk_data
        if current_hunk_header is None:
            return

        minus_count = 0
        plus_count = 0
        for hl in current_hunk_data:
            if hl.startswith('-'):
                minus_count += 1
            elif hl.startswith('+'):
                plus_count += 1
            elif hl.startswith('\\'):
                pass
            else:
                minus_count += 1
                plus_count += 1

        m = re.match(r'^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$', current_hunk_header)
        if m:
            start_minus = m.group(1)
            start_plus = m.group(2)
            rest = m.group(3)

            new_minus_str = f"{start_minus},{minus_count}" if minus_count != 1 else start_minus
            new_plus_str = f"{start_plus},{plus_count}" if plus_count != 1 else start_plus

            fixed_header = f"@@ -{new_minus_str} +{new_plus_str} @@{rest}"
            fixed_lines.append(fixed_header)
        else:
            fixed_lines.append(current_hunk_header)

        fixed_lines.extend(current_hunk_data)
        current_hunk_header = None
        current_hunk_data = []

    for line in lines:
        if line.startswith("@@ "):
            flush_hunk()
            current_hunk_header = line
        elif current_hunk_header is not None:
            current_hunk_data.append(line)
        else:
            if line.startswith("--- "):
                path = line[4:].strip()
                if path == "/dev/null":
                    fixed_lines.append(line)
                else:
                    if not file_exists:
                        fixed_lines.append("--- /dev/null")
                    else:
                        fixed_lines.append(f"--- a/{relative_path}")
            elif line.startswith("+++ "):
                path = line[4:].strip()
                if path == "/dev/null":
                    fixed_lines.append(line)
                else:
                    fixed_lines.append(f"+++ b/{relative_path}")
            else:
                fixed_lines.append(line)

    flush_hunk()
    return fixed_lines

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
        vim.command("file Git Diff")
        # Setting filetype to 'diff' helps with syntax highlighting
        vim.command("setlocal buftype=nofile filetype=diff noswapfile")

        # The output from git contains ANSI escape codes for color.
        # We place this raw output into the buffer.
        vim.current.buffer[:] = result.stdout.split("\n")

    except FileNotFoundError:
        util.display_message("Error: `git` command not found. Is it in your PATH?", error=True)
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)

def apply_patch(diff_content, project_root=None, silent=False):
    """
    Applies a unified diff patch and reloads affected buffers.
    Returns True if successful, False otherwise.
    """
    if not diff_content:
        msg = "Diff is empty. Nothing to apply."
        if not silent: util.display_message(msg, history=True)
        return False

    if not project_root:
        project_root = util.get_git_repo_root() or vim.eval("getcwd()")

    try:
        # Use patch command to apply the diff
        # -p1 strips the first path component (a/ and b/)
        # -N ignores patches that seem already applied
        # -r - rejects to stdout (avoids .rej files)
        result = subprocess.run(
            ["patch", "-p1", "-N", "-r", "-"],
            input=diff_content, text=True, check=False,
            capture_output=True, cwd=project_root
        )

        if result.returncode != 0:
            err_msg = f"Patch command failed. Please review the output and the diff.\nSTDOUT: {result.stdout}\nSTDERR: {result.stderr}"
            if not silent: util.display_message(err_msg, error=True)
            return False

        # Success
        util.display_message("Successfully applied modified diff.", history=True)

        # Parse diff to identify modified files for reloading
        modified_files = set()
        for line in diff_content.split("\n"):
            if line.startswith("--- a/"):
                path = line[len("--- a/"):].strip()
                if path != "/dev/null":
                    modified_files.add(path)
            elif line.startswith("+++ b/"):
                path = line[len("+++ b/"):].strip()
                if path != "/dev/null":
                    modified_files.add(path)

        for relative_path in modified_files:
            absolute_path = os.path.join(project_root, relative_path)

            # Clean up .orig files potentially created by patch
            orig_path = absolute_path + ".orig"
            if os.path.exists(orig_path):
                try:
                    os.remove(orig_path)
                except Exception:
                    pass

            normalized_target_path = os.path.realpath(absolute_path)
            for buf in vim.buffers:
                if buf.name and os.path.realpath(buf.name) == normalized_target_path:
                    # Reload buffer if visible
                    win_nr = vim.eval(f"bufwinnr({buf.number})")
                    if int(win_nr) > 0:
                        vim.command(f"{win_nr}wincmd w")
                        vim.command("e!")
                        vim.command("wincmd p")
                    else:
                        # Mark buffer to be reloaded when entered
                        vim.command(f"checktime {buf.number}")
                    break
        return True

    except FileNotFoundError:
        util.display_message("Error: `patch` command not found. Is it in your PATH?", error=True)
        return False
    except Exception as e:
        util.display_message(f"Error applying diff: {e}", error=True)
        return False

def apply_code(job_id=None):
    """
    Finds the 'Vimini Code' buffer, writes all specified file changes to
    disk, and reloads any affected open buffers. If an error occurs, the
    diff buffer is preserved for manual editing and re-application.
    """
    global _BUFFER_DATA_STORE
    util.log_info(f"apply_code(job_id={job_id})")
    diff_buffer = None

    # 1. Find all potential Vimini Code buffers
    candidates = []
    for buf in vim.buffers:
        if buf.name and 'Vimini Code' in os.path.basename(buf.name):
            candidates.append(buf)

    if not candidates:
        util.display_message("`Vimini Code` buffer not found. Was :ViminiCode run?", error=True)
        return

    # 2. Filter by job_id if provided, or handle selection logic
    if job_id is not None:
        target_candidates = []
        for buf in candidates:
            # Try matching by internal buffer variable
            try:
                bid = buf.vars.get("vimini_job_id")
                if bid is not None and _to_str(bid) == str(job_id):
                    target_candidates.append(buf)
                    continue
            except Exception:
                pass

            # Try matching by filename pattern "[{job_id}] Vimini Code"
            basename = os.path.basename(buf.name)
            if f"[{job_id}]" in basename:
                 target_candidates.append(buf)

        if not target_candidates:
            util.display_message(f"No Vimini Code buffer found for Job ID {job_id}.", error=True)
            return

        # If for some reason multiple buffers match the same ID, take the last one
        diff_buffer = target_candidates[-1]

    else:
        # No job ID provided.
        if vim.current.buffer in candidates:
            diff_buffer = vim.current.buffer
        elif len(candidates) > 1:
            # Multiple buffers exist: Error and list them
            msg = "Multiple Vimini Code buffers found. Please specify which job to apply using -j <job_id>.\nAvailable Jobs:\n"
            for buf in candidates:
                bid = "Unknown"
                try:
                    raw_bid = buf.vars.get("vimini_job_id")
                    if raw_bid is not None:
                        bid = _to_str(raw_bid)
                except Exception:
                    pass

                if not bid or bid == "Unknown":
                     m = re.search(r'\[(\d+)\]', os.path.basename(buf.name))
                     if m: bid = m.group(1)

                msg += f"- Job {bid} (Buffer {buf.number})\n"

            util.display_message(msg.strip(), error=True)
            return

        else:
            # Only one buffer exists
            diff_buffer = candidates[0]

    # 4. Extract diff content using separator
    project_root = _to_str(diff_buffer.vars.get("vimini_project_root", ""))
    if not project_root:
        project_root = util.get_git_repo_root() or vim.eval("getcwd()")

    separator_index = -1
    for i, line in enumerate(diff_buffer):
        if _DIFF_SEPARATOR in line:
            separator_index = i
            break

    if separator_index != -1:
        diff_content = "\n".join(diff_buffer[separator_index + 1:])
        # Ensure the patch content ends with a newline
        if diff_content and not diff_content.endswith('\n'):
            diff_content += '\n'
    else:
        err_msg = "DIFF section not found, did you remove the separator?"
        util.display_message(err_msg, error=True)
        return  # Preserve buffer

    if not diff_content:
        util.display_message("Diff is empty. Nothing to apply.", history=True)
        vim.command(f"bdelete! {diff_buffer.number}")
        if diff_buffer.number in _BUFFER_DATA_STORE:
            del _BUFFER_DATA_STORE[diff_buffer.number]
        return

    if apply_patch(diff_content, project_root):
        try:
            is_chat_patch = int(_to_str(diff_buffer.vars.get("vimini_is_chat_patch", 0)) or 0)
            if is_chat_patch == 1:
                diff_buffer.vars["vimini_patch_handled"] = 1
                req_id = _to_str(diff_buffer.vars["vimini_chat_job_id"])
                from vimini.chat import send_agent_approval
                send_agent_approval(True, req_id)
        except Exception as e:
            util.log_info(f"Error sending agent approval from apply_code: {e}")

        # Remove from data store
        if diff_buffer.number in _BUFFER_DATA_STORE:
            del _BUFFER_DATA_STORE[diff_buffer.number]

        # Cleanup
        vim.command(f"bdelete! {diff_buffer.number}")
