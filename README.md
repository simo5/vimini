# Vimini: Google Gemini Integration for Vim

Vimini is a Vim plugin that provides seamless integration with Google's
Gemini (Generative AI) models, allowing you to interact with AI directly
from your Vim editor. You can chat, generate code, get code reviews, and
even receive real-time autocomplete suggestions without leaving your
coding environment.

## Features

*   **Chat with Gemini**: Send prompts and receive responses in a new
    buffer.
*   **Context-Aware Code Generation**: Use all open buffers as context to
    generate code.
*   **Code Review**: Get AI-powered reviews for the code in your current
    buffer or from your git history.
*   **Git Integration**: Generate commit messages and view diffs using
    AI.
*   **Real-time Autocomplete**: Get code suggestions as you type in
    insert mode.
*   **List Models**: Easily view all available Gemini models.
*   **API Key Management**: Configure your Gemini API key securely.
*   **Live "Thinking" View**: Optionally watch the AI's thought process in
    real-time during code generation and reviews.

## Requirements

*   Vim (or Neovim) with Python 3 support.
*   Python 3.6+
*   The `google-genai` Python library. You can install it via pip:
    ```bash
    pip install google-genai
    ```
*   `git` must be installed and in your `PATH` for the Git-related
    commands.
*   `ripgrep` (`rg`) must be installed and in your `PATH` for the Ripgrep
    integration commands.

## Installation

You can install Vimini using your preferred Vim plugin manager.

**Using [Vim-Plug](https://github.com/junegunn/vim-plug):**

1.  Add the following line to your `.vimrc` or `init.vim`:
    ```vim
    call plug#begin()
    Plug 'your-github-username/vimini.vim' " Replace with the actual repo path
    call plug#end()
    ```
2.  Run `:PlugInstall` in Vim.

**Using [Packer.nvim](https://github.com/wbthomason/packer.nvim):**

1.  Add the following to your `init.lua` (for Neovim) or `plugins.lua`:
    ```lua
    use 'your-github-username/vimini.vim' " Replace with the actual repo path
    ```
2.  Run `:PackerSync` or `:PackerInstall` in Neovim.

*(Note: Replace `your-github-username/vimini.vim` with the actual
repository path once published.)*

## Configuration

Vimini requires your Google Gemini API key and allows for several
customizations.

1.  **API Key (`g:vimini_api_key`)**:
    You can set your API key directly in your `.vimrc` or `init.vim`:
    ```vim
    let g:vimini_api_key = 'YOUR_API_KEY_HERE'
    ```
    **Alternatively**, Vimini will also look for the API key in a file
    named `~/.config/gemini.token`. This is the recommended and more
    secure approach. Just place your API key (and nothing else) into
    that file:
    ```
    # ~/.config/gemini.token
    YOUR_API_KEY_HERE
    ```

2.  **Default Model (`g:vimini_model`)**:
    Specify the default Gemini model you want to use. The default is
    `gemini-2.5-flash`. You can list available models using `:ViminiListModels`.
    ```vim
    let g:vimini_model = 'gemini-2.5-flash' " Or 'gemini-2.5-pro', etc.
    ```

3.  **Generation Temperature (`g:vimini_temperature`)**:
    Control the creativity of the AI's responses for commands like
    `:ViminiCode`, `:ViminiCommit`, and `:ViminiReview`. The temperature is a value between
    0.0 and 2.0. Higher values (e.g., 1.0) make the output more random
    and creative, while lower values (e.g., 0.2) make it more focused
    and deterministic. By default, it is not set, allowing the model to
    use its default temperature.
    ```vim
    " Set a specific temperature for generation
    let g:vimini_temperature = 0.7
    ```

4.  **Thinking Display (`g:vimini_thinking`)**:
    Control whether the AI's "thinking" process is displayed in a
    separate buffer during code generation and reviews.
    ```vim
    " Show the 'Vimini Thoughts' buffer. (Default)
    let g:vimini_thinking = 'on'

    " Hide the 'Vimini Thoughts' buffer.
    let g:vimini_thinking = 'off'
    ```
    This can also be controlled with the `:ViminiThinking` command.

5.  **Autocomplete (`g:vimini_autocomplete`)**:
    Enable or disable the real-time autocomplete feature. It is disabled
    by default.
    ```vim
    " Enable autocomplete feature
    let g:vimini_autocomplete = 'on'

    " Disable autocomplete feature (Default)
    let g:vimini_autocomplete = 'off'
    ```
    This can also be controlled with the `:ViminiToggleAutocomplete` command.

6.  **Logging (`g:vimini_logging` and `g:vimini_log_file`)**:
    Control whether Vimini logs its activity to a file, which can be
    useful for debugging. Logging is disabled by default.
    ```vim
    " Enable logging (Default is 'off')
    let g:vimini_logging = 'on'

    " Set a custom path for the log file (Default is '~/.var/vimini/vimini.log')
    let g:vimini_log_file = '/path/to/your/vimini.log'
    ```
    Logging can also be controlled at runtime with the `:ViminiToggleLogging` command.

7.  **Commit Author (`g:vimini_commit_author`)**:
    Customize the `Co-authored-by` trailer used in `:ViminiCommit`.
    ```vim
    " Set a custom author trailer (Default is 'Co-authored-by: Gemini <gemini@google.com>')
    let g:vimini_commit_author = 'Co-authored-by: My AI Assistant <ai@example.com>'
    ```

8.  **Context Files (`g:context_files`)**:
    In addition to all files currently open in Vim buffers, you can specify a list of files to always include as context for AI commands like `:ViminiCode`. This is useful for providing the AI with important project files (like configurations, core utilities, or type definitions) without needing to have them open.
    ```vim
    " Always include these files as context for code generation tasks
    let g:context_files = ['package.json', 'src/main.js', 'src/utils/api.js']
    ```

## Usage

Vimini exposes several commands for interacting with the Gemini API:

### `:ViminiListModels`

Lists all available Gemini models in a new split window. This is
useful for knowing which models you can set for `g:vimini_model`.

```vim
:ViminiListModels
```

### `:ViminiChat {prompt}`

Sends a `prompt` to the configured Gemini model and displays the AI's
response in a new vertical split buffer.

```vim
:ViminiChat What is the capital of France?
```

### `:ViminiThinking [on|off]`

Toggles or sets the display of the AI's real-time thought process
during code generation and reviews. When enabled, a `Vimini Thoughts` buffer will
appear alongside the main result buffer.

```vim
" Toggle the current setting (on -> off, off -> on)
:ViminiThinking

" Explicitly turn the thinking display on
:ViminiThinking on

" Explicitly turn the thinking display off
:ViminiThinking off
```

### `:ViminiToggleLogging [on|off]`

Toggles or sets the logging feature. When enabled, Vimini will log API
requests, responses, and other internal actions to the file specified
by `g:vimini_log_file`. This is primarily useful for debugging.

```vim
" Toggle the current setting (on -> off, off -> on)
:ViminiToggleLogging

" Explicitly turn logging on
:ViminiToggleLogging on

" Explicitly turn logging off
:ViminiToggleLogging off
```

### `:ViminiCode {prompt}`

Takes the content of all open buffers (and any files specified in `g:context_files`) as context, along with your
`prompt`, and asks the Gemini model to generate code. The result is
streamed into several new buffers:
*   `Vimini Diff`: A diff view showing the proposed changes across one or more files.
*   `Vimini Thoughts` (Optional): If `g:vimini_thinking` is `on`, this buffer shows the AI's internal monologue as it works.

This command is ideal for asking the AI to refactor, debug, or extend
your current code.

```vim
:ViminiCode Please refactor this function to be more concise
```

After running `:ViminiCode`, you can use the following command to
apply the changes:

#### `:ViminiApply`
*   `:ViminiApply`: Applies the AI-generated changes to the relevant files on disk and reloads them in Vim if they are open.

This command will close the temporary `Vimini Diff` and
`Vimini Thoughts` buffers.

### `:ViminiContextFiles`

Opens an interactive file manager in a new split window to easily manage the list of files in `g:context_files`. This allows you to add or remove files that should always be included as context for AI commands, without manually editing your `.vimrc`.

**How to use the manager:**
*   Navigate the file system like a normal file explorer.
*   Files marked with a `C` prefix are currently in the context list.
*   **`<Enter>` on a file**: Toggles its inclusion in the context.
*   **`<Enter>` on a directory**: Navigates into that directory.
*   **`l`**: Shows a popup with a summary of the currently active and pending context files.

When you are done, simply close the window (e.g., with `:q`). A popup will ask you to confirm whether to save the changes to `g:context_files` for the current Vim session.

```vim
:ViminiContextFiles
```

### `:ViminiReview [-c <git_objects>] [{prompt}]`

Sends content to the Gemini model for a code review. This command has two modes:

1.  **Current Buffer Review**: If no `-c` option is provided, it sends the content of the current buffer for review.
2.  **Git Object Review**: If the `-c <git_objects>` option is provided, it reviews the output of `git show <git_objects>`. `<git_objects>` can be any valid git object reference, like a commit hash, branch name, or `HEAD~1`.

You can add an optional `{prompt}` to guide the AI's review. The review will be displayed in a new vertical split buffer. If `g:vimini_thinking` is `on`, an additional buffer showing the AI's thought process will also be opened.

**Examples:**

```vim
" Review the current buffer for performance issues
:ViminiReview Check for performance issues.

" Perform a general review of the current buffer
:ViminiReview

" Review the changes in the latest commit
:ViminiReview -c HEAD

" Review changes from two commits ago and ask for security vulnerabilities
:ViminiReview -c HEAD~2 "Check for security vulnerabilities"
```

### Autocomplete

Vimini can provide real-time, context-aware code completions as you
type in insert mode. This feature is disabled by default.

> **Note:** The autocomplete command is experimental and still a little buggy and should be enabled with care.

#### `:ViminiToggleAutocomplete [on|off]`

Toggles or sets the real-time autocomplete feature. When enabled, Vimini
will automatically request a completion after you stop typing in insert
mode for a short period. The suggestion will be displayed as ghost text.

```vim
" Toggle the current setting (on -> off, off -> on)
:ViminiToggleAutocomplete

" Explicitly turn autocomplete on
:ViminiToggleAutocomplete on

" Explicitly turn autocomplete off
:ViminiToggleAutocomplete off
```

### Git Integration

Vimini offers commands to integrate with your Git workflow.

#### `:ViminiDiff`

Shows the output of `git diff` for the current repository in a new
split window. This allows you to see unstaged changes without leaving
Vim.

```vim
:ViminiDiff
```

#### `:ViminiCommit [-n] [-r]`

Automates the commit process using AI. This command has two main modes:

**Default Mode (Creating a new commit):**

When run without flags (or with `-n`), it automates the creation of a new commit:
1.  Stages all current changes (`git add .`).
2.  Generates a commit message based on the staged diff.
3.  Displays the generated message for confirmation (`y/n`).
4.  If confirmed, it commits the changes with the generated message.

**Regenerate/Amend Mode (`-r`):**

When run with the `-r` flag, it regenerates the commit message for the last commit (`HEAD`) and amends it:
1.  It gets the diff from the `HEAD` commit.
2.  It generates a new commit message based on that diff.
3.  It displays the message for confirmation (`y/n`).
4.  If confirmed, it amends the `HEAD` commit with the new message. This is useful for quickly rewriting a commit message you just made.

**Options:**

*   **`-n`**: Omit the `Co-authored-by` trailer for a specific commit. By default, a trailer is appended and can be configured with `g:vimini_commit_author`.
*   **`-r`**: Enable the regenerate/amend mode.

**Examples:**
```vim
" Stage changes and generate a commit message for a new commit
:ViminiCommit

" Do the same, but without the co-author trailer
:ViminiCommit -n

" Regenerate the message for the HEAD commit and amend it
:ViminiCommit -r
```

### Ripgrep Integration (Highly Experimental)

> **Note:** The `ViminiRipGrep` command is basically an AI-assisted translation of the code at [https://gitlab.com/aarcange/ripgrep-edit](https://gitlab.com/aarcange/ripgrep-edit). Credit for the original project goes to Andrea Arcangeli.

This powerful feature combines `ripgrep`'s fast searching with Gemini's AI-powered code modification capabilities, allowing you to perform project-wide refactoring from a single prompt.

#### `:ViminiRipGrep {regex} {prompt}`

This command initiates an AI-assisted search and replace workflow:
1.  It runs `ripgrep` with the given `{regex}` to find all occurrences in your project.
2.  The search results, including a few lines of context, are placed into a new temporary buffer named `ViminiRipGrep`.
3.  This buffer's content is then sent to Gemini along with your `{prompt}` for modification.
4.  Gemini applies your requested changes to the buffer content. You can then review and even manually edit the proposed changes directly in the `ViminiRipGrep` buffer before applying them.

**Example:**
To rename `old_function_name` to `new_function_name` across your entire project:
```vim
:ViminiRipGrep 'old_function_name' 'rename this function to new_function_name'
```

#### `:ViminiRipGrepApply`
Once you are satisfied with the AI-generated changes in the `ViminiRipGrep` buffer, run this command. It will apply the modifications to the actual files on disk and close the temporary buffer.

```vim
:ViminiRipGrepApply
```

### `:ViminiFiles`

Opens an interactive buffer to manage files uploaded to Google for use with
the Gemini API. This allows you to interact with a persistent set of files
that the model can use for context across different prompts.

The command opens a `Vimini Files` buffer that lists all your uploaded files.

**How to use the manager:**
*   **`i`**: Shows detailed information about the file under the cursor in a
    new window.
*   **`d`**: Deletes the file under the cursor from the server. The file list
    is refreshed automatically.
*   **`q`**: Closes the `Vimini Files` buffer.

```vim
:ViminiFiles
```

## Sheperd's note
Most of the code here is generated by Gemini itself, I only provide
guidance and occasioanly edit some small bit where it is easier then
asking

Also, it's important to remember that AI, much like a well-fed cat,
requires a steady stream of attention and high-quality prompts to
perform its best tricks. Neglect it, and you might just find it
napping on your keyboard when you need it most.

--
Simo.
