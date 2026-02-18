import vim
import subprocess
import shlex
import os
import re
from vimini import util, context, code

def _construct_review_prompt(uploaded_files, prompt, content_source_description, security_focus):
    """
    Constructs the prompt for the review.
    """
    context_files_section = ""
    if uploaded_files:
        context_file_names = sorted([f.display_name for f in uploaded_files])
        file_list_str = "\n".join(f"- {name}" for name in context_file_names)
        context_files_section = (
            "The following files have been uploaded for context and contain the full, "
            "up-to-date source code for the changes being reviewed:\n"
            f"{file_list_str}\n\n"
        )

    if security_focus:
        review_instructions = (
            f"Please review {content_source_description} exclusively for potential security issues or hazards. "
            "Focus on identifying vulnerabilities, insecure coding practices, and potential attack vectors. "
            "Provide clear, actionable suggestions for mitigation. Do not comment on code style, "
            "performance, or other non-security aspects."
        )
    else:
        review_instructions = (
            f"Please review {content_source_description} for potential issues, "
            "improvements, best practices, and any possible bugs. "
            "Provide a concise summary and actionable suggestions."
        )

    prompt_text = (
        f"{review_instructions}\n\n"
        f"{context_files_section}"
        "--- CONTENT TO REVIEW ---\n"
        "{{REVIEW_CONTENT}}\n"
        "--- END CONTENT TO REVIEW ---"
        f"\n{prompt}\n"
    )
    return prompt_text

def review(prompt, git_objects=None, security_focus=False, verbose=False, temperature=None, save=False, save_path=None):
    """
    Sends content to the Gemini API for a code review.
    If 'save' is True and 'git_objects' are provided, saves reviews to 'save_path'.
    """
    util.log_info(f"review({prompt}, git_objects='{git_objects}', security_focus={security_focus}, verbose={verbose}, temperature={temperature}, save={save}, save_path='{save_path}')")
    try:
        client = util.get_client()
        if not client:
            return

        # --- BATCH SAVE MODE ---
        if git_objects and save:
            repo_path = util.get_git_repo_root()
            if not repo_path:
                return

            objects_to_resolve = shlex.split(git_objects)
            
            # Check if a range is specified. If not, we don't want to walk the whole history.
            rev_list_args = []
            if not any(".." in obj for obj in objects_to_resolve):
                rev_list_args.append('--no-walk')

            cmd = ['git', '-C', repo_path, 'rev-list', '--reverse'] + rev_list_args + objects_to_resolve
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if result.returncode != 0:
                error_message = (result.stderr or "git rev-list failed.").strip()
                util.display_message(f"Git error: {error_message}", error=True)
                return

            commit_list = [sha for sha in result.stdout.strip().split('\n') if sha]
            if not commit_list:
                util.display_message(f"No commits found for range '{git_objects}'.", history=True)
                return

            # Determine Save Directory
            target_dir = repo_path
            path_config = save_path

            # If path not provided via argument, check global variable
            if not path_config:
                path_config = vim.eval("get(g:, 'vimini_review_path', '')")

            if path_config:
                expanded = os.path.expanduser(os.path.expandvars(path_config))
                # os.path.join handles absolute paths in the second argument by discarding the first
                target_dir = os.path.join(repo_path, expanded)

                if not os.path.exists(target_dir):
                    try:
                        os.makedirs(target_dir, exist_ok=True)
                    except Exception as e:
                        util.display_message(f"Error creating directory {target_dir}: {e}", error=True)
                        return

            total_commits = len(commit_list)
            current_review_accumulator = []

            def process_batch_commit(index):
                nonlocal current_review_accumulator
                if index >= total_commits:
                    util.display_message("All reviews completed and saved.", history=True)
                    return

                commit_sha = commit_list[index]
                patch_num = index + 1
                status_msg = f"Reviewing commit {patch_num}/{total_commits}: {commit_sha[:7]}... (Async)"
                util.display_message(status_msg)

                # Reset accumulator
                current_review_accumulator = []

                # Get content (Synchronous for now to setup the prompt)
                cmd_show = ['git', '-C', repo_path, 'show', commit_sha]
                result_show = subprocess.run(cmd_show, capture_output=True, text=True, check=False)
                if result_show.returncode != 0:
                    error_message = (result_show.stderr or "git show failed.").strip()
                    util.display_message(f"Skipping {commit_sha[:7]}: {error_message}", error=True, history=True)
                    process_batch_commit(index + 1)
                    return
                review_content_single = result_show.stdout

                # Get context
                uploaded_files_single = []
                cmd_files = ['git', '-C', repo_path, 'show', '--name-only', '--format=', commit_sha]
                result_files = subprocess.run(cmd_files, capture_output=True, text=True, check=False)
                if result_files.returncode == 0:
                    changed_files_relative = [f for f in result_files.stdout.strip().split('\n') if f]
                    if changed_files_relative:
                        context_files_to_upload = [os.path.join(repo_path, rel_path) for rel_path in changed_files_relative]
                        uploaded_files_single = context.upload_context_files(client, file_paths_to_include=context_files_to_upload) or []

                # Generate Prompt
                prompt_template = _construct_review_prompt(
                    uploaded_files_single, prompt,
                    f"the output of `git show {commit_sha[:7]}`",
                    security_focus
                )
                prompt_text = prompt_template.replace("{{REVIEW_CONTENT}}", review_content_single)
                full_prompt = [prompt_text, *uploaded_files_single]

                def on_chunk(text):
                    current_review_accumulator.append(text)

                def on_finish():
                    # Save
                    status_msg = ""
                    try:
                        subject_cmd = ['git', '-C', repo_path, 'log', '-1', '--pretty=%s', commit_sha]
                        subject_result = subprocess.run(subject_cmd, capture_output=True, text=True, check=False)
                        subject = subject_result.stdout.strip() if subject_result.returncode == 0 else "commit"

                        sanitized_subject = re.sub(r'[^a-zA-Z0-9]+', '-', subject).strip('-').lower()
                        sanitized_subject = sanitized_subject[:50]

                        filename = f"{patch_num:04d}-{sanitized_subject}.review.txt"
                        filepath = os.path.join(target_dir, filename)

                        content = "".join(current_review_accumulator)
                        with open(filepath, "w", encoding='utf-8') as f:
                            f.write(content)
                        status_msg = f"Saved review to {filename}"
                    except Exception as e:
                        status_msg = f"Error saving {filename}: {e}"

                    # Trigger next commit review
                    process_batch_commit(index + 1)
                    return status_msg

                def on_error(msg):
                    process_batch_commit(index + 1)
                    return f"Error reviewing {commit_sha[:7]}: {msg}"

                kwargs = util.create_generation_kwargs(
                    contents=full_prompt,
                    temperature=temperature,
                    verbose=verbose
                )

                job_name = f"Review: {commit_sha[:7]} {prompt}"

                util.start_async_job(client, kwargs, {
                    'on_chunk': on_chunk,
                    'on_finish': on_finish,
                    'on_error': on_error,
                    'status_message': status_msg
                }, job_name=job_name)

            process_batch_commit(0)
            return

        # --- INTERACTIVE MODE (ASYNC) ---
        review_content = ""
        content_source_description = ""
        uploaded_files = []

        if git_objects:
            repo_path = util.get_git_repo_root()
            if not repo_path:
                return

            objects_to_show = shlex.split(git_objects)
            for obj in objects_to_show:
                if obj.startswith('-'):
                    util.display_message("Security error: Git options (like flags starting with '-') are not allowed.", error=True)
                    return

            cmd = ['git', '-C', repo_path, 'show'] + objects_to_show
            util.display_message(f"Running git show {git_objects}... ")
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if result.returncode != 0:
                error_message = (result.stderr or "git show failed.").strip()
                util.display_message(f"Git error: {error_message}", error=True)
                return
            review_content = result.stdout

            util.display_message("Getting changed files for context...")
            cmd_files = ['git', '-C', repo_path, 'show', '--name-only', '--format='] + objects_to_show
            result_files = subprocess.run(cmd_files, capture_output=True, text=True, check=False)
            if result_files.returncode == 0:
                changed_files_relative = [f for f in result_files.stdout.strip().split('\n') if f]
                if changed_files_relative:
                    context_files_to_upload = [os.path.join(repo_path, rel_path) for rel_path in changed_files_relative]
                    uploaded_files = context.upload_context_files(client, file_paths_to_include=context_files_to_upload) or []

            util.display_message("")
            util.new_split()
            display_objects = (git_objects[:40] + '..') if len(git_objects) > 40 else git_objects
            vim.command(f'file Vimini Git Review Target: {display_objects}')
            vim.command('setlocal buftype=nofile filetype=diff noswapfile')
            vim.current.buffer[:] = review_content.split('\n')
            vim.command('normal! 1G')

            content_source_description = f"the output of `git show {git_objects}`"
        else:
            review_content = "\n".join(vim.current.buffer[:])
            original_filetype = vim.eval('&filetype') or 'text'
            content_source_description = f"the following {original_filetype} code"

        if not review_content.strip():
            util.display_message("Nothing to review.", history=True)
            return

        job_name = f"Review: {git_objects if git_objects else 'current buffer'} {prompt}"
        job_id = util.reserve_next_job_id(job_name)

        thoughts_buf_num = -1
        if verbose:
            try:
                thoughts_buf_num = util.create_thoughts_buffer(job_id)
            except Exception as e:
                util.display_message(f"Error creating thoughts buffer: {e}", error=True)

        util.new_split()
        vim.command('file Vimini Review')
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
        review_buffer = vim.current.buffer
        review_buffer[:] = ['']

        # Prepare Async Job
        prompt_template = _construct_review_prompt(uploaded_files, prompt, content_source_description, security_focus)
        prompt_text = prompt_template.replace("{{REVIEW_CONTENT}}", review_content)
        full_prompt = [prompt_text, *uploaded_files]

        kwargs = util.create_generation_kwargs(
            contents=full_prompt,
            temperature=temperature,
            verbose=verbose
        )

        review_buf_num = review_buffer.number

        def on_chunk(text):
            util.append_to_buffer(review_buf_num, text)

        def on_thought(text):
            if verbose and thoughts_buf_num != -1:
                util.append_to_buffer(thoughts_buf_num, text)

        def on_error(msg):
            return f"Error: {msg}"

        util.display_message("Processing... (Async)")
        util.start_async_job(client, kwargs, {
            'on_chunk': on_chunk,
            'on_thought': on_thought,
            'on_error': on_error
        }, job_id=job_id)

    except FileNotFoundError:
        util.display_message("Error: `git` command not found. Is it in your PATH?", error=True)
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)
