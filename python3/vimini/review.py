import vim
import subprocess
import shlex
from vimini import util

def review(prompt, git_objects=None, verbose=False, temperature=None):
    """
    Sends content to the Gemini API for a code review.
    If git_objects are provided, it reviews the output of `git show <objects>`.
    Otherwise, it reviews the content of the current buffer.
    The review is displayed in a new buffer, streaming thoughts if verbose.
    """
    util.log_info(f"review({prompt}, git_objects='{git_objects}', verbose={verbose}, temperature={temperature})")
    try:
        client = util.get_client()
        if not client:
            return

        review_content = ""
        content_source_description = ""

        if git_objects:
            # Handle review of git objects
            repo_path = util.get_git_repo_root()
            if not repo_path:
                return # Error message is handled by util.get_git_repo_root()

            # Security Hardening: Prevent command injection via git flags.
            # The user should only provide git objects (hashes, branches, etc.), not options.
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
            util.display_message("") # Clear message

            review_content = result.stdout

            # As requested, open a new buffer with the git show output, which becomes the context
            util.new_split()
            # Truncate for display if the object string is too long
            display_objects = (git_objects[:40] + '..') if len(git_objects) > 40 else git_objects
            vim.command(f'file Vimini Git Review Target: {display_objects}')
            vim.command('setlocal buftype=nofile filetype=diff noswapfile')
            vim.current.buffer[:] = review_content.split('\n')
            vim.command('normal! 1G') # Go to top of new buffer

            content_source_description = f"the output of `git show {git_objects}`"
        else:
            # Handle review of the current buffer (original behavior)
            review_content = "\n".join(vim.current.buffer[:])
            original_filetype = vim.eval('&filetype') or 'text'
            content_source_description = f"the following {original_filetype} code"

        if not review_content.strip():
            util.display_message("Nothing to review.", history=True)
            return

        # Construct the full prompt for the API.
        full_prompt = (
            f"Please review {content_source_description} for potential issues, "
            "improvements, best practices, and any possible bugs. "
            "Provide a concise summary and actionable suggestions.\n\n"
            "--- CONTENT TO REVIEW ---\n"
            f"{review_content}\n"
            "--- END CONTENT TO REVIEW ---"
            f"\n{prompt}\n"
        )

        thoughts_buffer = None
        if verbose:
            # Create the Vimini Thoughts buffer before calling the model.
            util.new_split()
            vim.command('file Vimini Thoughts')
            vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
            thoughts_buffer = vim.current.buffer
            thoughts_buffer[:] = ['']

        # Create the Vimini Review buffer. This becomes the active window.
        util.new_split()
        vim.command('file Vimini Review')
        vim.command('setlocal buftype=nofile filetype=markdown noswapfile')
        review_buffer = vim.current.buffer # Reference the new review buffer
        review_buffer[:] = ['']

        # Get window numbers for faster switching during streaming.
        thoughts_win_nr = None
        if verbose:
            thoughts_win_nr = vim.eval(f"bufwinnr({thoughts_buffer.number})")
        review_win_nr = vim.eval(f"bufwinnr({review_buffer.number})")

        # Display a Processing.. message so users know they have to wait
        util.display_message("Processing...")

        # Set up the API call arguments
        kwargs = util.create_generation_kwargs(
            contents=full_prompt,
            temperature=temperature,
            verbose=verbose
        )

        # Use generate_content_stream()
        response_stream = client.models.generate_content_stream(**kwargs)

        for chunk in response_stream:
            if not chunk.candidates:
                continue
            for part in chunk.candidates[0].content.parts:
                if not part.text:
                    continue

                is_thought = hasattr(part, 'thought') and part.thought
                # If we're not in verbose mode, we don't care about thought parts.
                if is_thought and not verbose:
                    continue

                target_buffer = thoughts_buffer if is_thought else review_buffer
                # This should not be None due to the check above, but for safety.
                if not target_buffer:
                    continue

                # Switch to the window displaying the buffer being updated.
                target_win_nr = thoughts_win_nr if is_thought else review_win_nr
                if int(target_win_nr) > 0:
                    vim.command(f"{target_win_nr}wincmd w")

                # Split incoming text by newlines to handle chunks that span multiple lines
                new_lines = part.text.split('\n')

                # Append the first part of the new text to the current last line in the buffer
                target_buffer[-1] += new_lines[0]

                # If the chunk contained one or more newlines, add the rest as new lines
                if len(new_lines) > 1:
                    target_buffer.append(new_lines[1:])

                # Move cursor to the end and scroll view to keep the last line visible.
                vim.command('normal! Gz-')
                util.display_message("Processing...")

        util.display_message("") # Clear the thinking message

    except FileNotFoundError:
        util.display_message("Error: `git` command not found. Is it in your PATH?", error=True)
    except Exception as e:
        util.display_message(f"Error: {e}", error=True)
