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

def _generate_review_sync(client, review_content, uploaded_files, prompt, content_source_description, security_focus, verbose, temperature, review_buffer, thoughts_buffer):
    """
    Generates the review synchronously (blocking), writing to buffers as it goes.
    Used for the batch --save mode.
    """
    prompt_template = _construct_review_prompt(uploaded_files, prompt, content_source_description, security_focus)
    prompt_text = prompt_template.replace("{{REVIEW_CONTENT}}", review_content)
    full_prompt = [prompt_text, *uploaded_files]

    util.display_message("Processing...")

    kwargs = util.create_generation_kwargs(
        contents=full_prompt,
        temperature=temperature,
        verbose=verbose
    )

    try:
        response_stream = client.models.generate_content_stream(**kwargs)

        for chunk in response_stream:
            if not chunk.candidates:
                continue
            for part in chunk.candidates[0].content.parts:
                if not part.text:
                    continue

                is_thought = hasattr(part, 'thought') and part.thought
                if is_thought and not verbose:
                    continue

                target_buffer = thoughts_buffer if is_thought else review_buffer
                if target_buffer:
                     code.append_to_buffer(target_buffer.number, part.text)

                util.display_message("Processing...")
    except Exception as e:
        util.display_message(f"Error generating review: {e}", error=True)

    util.display_message("")

def review(prompt, git_objects=None, security_focus=False, verbose=False, temperature=None, save=False):
    """
    Sends content to the Gemini API for a code review.
    """
    util.log_info(f"review({prompt}, git_objects='{git_objects}', security_focus={security_focus}, verbose={verbose}, temperature={temperature}, save={save})")
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
            cmd = ['git', '-C', repo_path, 'rev-list', '--reverse'] + objects_to_resolve
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)

            if result.returncode != 0:
                error_message = (result.stderr or "git rev-list failed.").strip()
                util.display_message(f"Git error: {error_message}", error=True)
                return

            commit_list = [sha for sha in result.stdout.strip().split('\n') if sha]
            if not commit_list:
                util.display_message(f"No commits found for range '{git_objects}'.", history=True)
                return

            # Create buffers once
            thoughts_buffer = None
            if verbose:
                util.new_split()
                vim.command('file Vimini Thoughts')
                vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
                thoughts_buffer = vim.current.buffer
            util.new_split()
            vim.command('file Vimini Review')
            vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
            review_buffer = vim.current.buffer

            total_commits = len(commit_list)
            for i, commit_sha in enumerate(commit_list):
                patch_num = i + 1
                util.display_message(f"Reviewing commit {patch_num}/{total_commits}: {commit_sha[:7]}...")

                # Clear buffers
                if thoughts_buffer:
                    thoughts_buffer[:] = ['']
                review_buffer[:] = ['']

                # Get content
                cmd_show = ['git', '-C', repo_path, 'show', commit_sha]
                result_show = subprocess.run(cmd_show, capture_output=True, text=True, check=False)
                if result_show.returncode != 0:
                    error_message = (result_show.stderr or "git show failed.").strip()
                    util.display_message(f"Skipping {commit_sha[:7]}: {error_message}", error=True, history=True)
                    continue
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

                # Generate Synchronously
                _generate_review_sync(
                    client, review_content_single, uploaded_files_single, prompt,
                    f"the output of `git show {commit_sha[:7]}`",
                    security_focus, verbose, temperature, review_buffer, thoughts_buffer
                )

                # Save
                subject_cmd = ['git', '-C', repo_path, 'log', '-1', '--pretty=%s', commit_sha]
                subject_result = subprocess.run(subject_cmd, capture_output=True, text=True, check=True)
                subject = subject_result.stdout.strip()

                sanitized_subject = re.sub(r'[^a-zA-Z0-9]+', '-', subject).strip('-').lower()
                sanitized_subject = sanitized_subject[:50]

                filename = f"{patch_num:04d}-{sanitized_subject}.review.txt"
                filepath = os.path.join(repo_path, filename)

                content = "\n".join(review_buffer[:])
                try:
                    with open(filepath, "w", encoding='utf-8') as f:
                        f.write(content)
                    util.display_message(f"Saved review to {filename}", history=True)
                except IOError as e:
                    util.display_message(f"Error saving file {filename}: {e}", error=True, history=True)

            util.display_message("All reviews completed and saved.", history=True)
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

        thoughts_buffer = None
        if verbose:
            util.new_split()
            vim.command('file Vimini Thoughts')
            vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
            thoughts_buffer = vim.current.buffer
            thoughts_buffer[:] = ['']

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
        thoughts_buf_num = thoughts_buffer.number if thoughts_buffer else -1

        def on_chunk(text):
            code.append_to_buffer(review_buf_num, text)

        def on_thought(text):
            if verbose and thoughts_buf_num != -1:
                code.append_to_buffer(thoughts_buf_num, text)

        def on_error(msg):
            util.display_message(f"Error: {msg}", error=True)

        util.display_message("Processing... (Async)")
        code.start_async_job(client, kwargs, {
            'on_chunk': on_chunk,
            'on_thought': on_thought,
            'on_error': on_error
        })

    except FileNotFoundError:
        util.display_message("Error: `git` command not found. Is it in your PATH?", error=True)
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)