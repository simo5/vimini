# Vimini: Google Gemini Integration for Vim

Vimini is a Vim plugin that provides seamless integration with Google's
Gemini (Generative AI) models, allowing you to interact with AI directly
from your Vim editor. You can chat, generate code, get code reviews, and
even receive real-time autocomplete suggestions without leaving your
coding environment.

## Features

*   **Chat with Gemini**: Opens the chat buffer for interactive mode.
*   **Context-Aware Code Generation**: Use all open buffers as context to
    generate code.
*   **Code Review**: Get AI-powered reviews for the code in your current
    buffer, git history, GitHub PRs, or GitLab MRs.
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
    Plug 'simo5/vimini'
    call plug#end()
    ```
2.  Run `:PlugInstall` in Vim.

**Using [Packer.nvim](https://github.com/wbthomason/packer.nvim):**

1.  Add the following to your `init.lua` (for Neovim) or `plugins.lua`:
    ```lua
    use 'simo5/vimini'
    ```
2.  Run `:PackerSync` or `:PackerInstall` in Neovim.

## Configuration

Vimini requires your Google Gemini API key and allows for several
customizations.

1.  **API Key (`~/.config/gemini.token`)**:
    Vimini loads your Google Gemini API key from `~/.config/gemini.token`.
    Place your API key (and nothing else) into that file:
    ```
    # ~/.config/gemini.token
    YOUR_API_KEY_HERE
    ```

2.  **Default Model (`~/.var/vimini/config.json`)**:
    The default model is configured per backend in the global Vimini configuration
    file under each backend's `default_model` field. Setting `g:vimini_model` in
    your Vim config is deprecated and no longer controls startup model selection.
    Use `:ViminiGlobalConfig` or press `D` on a model in `:ViminiListModels` to
    save it as the active backend's default model.

3.  **Thinking Display (`g:vimini_thinking`)**:
    Control whether the AI's "thinking" process is displayed in a
    separate buffer during code generation and reviews.
    ```vim
    " Show the 'Vimini Thoughts' buffer. (Default)
    let g:vimini_thinking = 'on'

    " Hide the 'Vimini Thoughts' buffer.
    let g:vimini_thinking = 'off'
    ```
    This can also be controlled with the `:ViminiThinking` command.

4.  **Autocomplete (`g:vimini_autocomplete`)**:
    Enable or disable the real-time autocomplete feature. It is disabled
    by default.
    ```vim
    " Enable autocomplete feature
    let g:vimini_autocomplete = 'on'

    " Disable autocomplete feature (Default)
    let g:vimini_autocomplete = 'off'
    ```
    This can also be controlled with the `:ViminiToggleAutocomplete` command.

5.  **Logging (`g:vimini_logging` and `g:vimini_log_file`)**:
    Control whether Vimini logs its activity to a file, which can be
    useful for debugging. Logging is disabled by default.
    ```vim
    " Enable logging (Default is 'off')
    let g:vimini_logging = 'on'

    " Set a custom path for the log file (Default is '~/.var/vimini/vimini.log')
    let g:vimini_log_file = '/path/to/your/vimini.log'
    ```
    Logging can also be controlled at runtime with the `:ViminiToggleLogging` command.

6.  **Commit Author (`g:vimini_commit_author`)**:
    Customize the `Co-authored-by` trailer used in `:ViminiCommit`.
    ```vim
    " Set a custom author trailer (Default is 'Co-authored-by: Gemini <gemini@google.com>')
    let g:vimini_commit_author = 'Co-authored-by: My AI Assistant <ai@example.com>'
    ```

7.  **Review Max Turns (`g:vimini_review_max_turns`)**:
    Set the maximum number of tool iterations for review agents. The default is `30`.
    ```vim
    let g:vimini_review_max_turns = 30
    ```

8.  **Default Git Remote (`g:vimini_default_remote`)**:
    Set a default git remote to fetch GitHub PRs or GitLab MRs from when using `:ViminiReview` without the `--remote` flag.
    If not configured, Vimini will check available remotes and use `upstream` (preferred for fork workflows), or the
    canonical remote for the current branch, falling back to default branches (`main`/`master`) or `origin`.
    ```vim
    let g:vimini_default_remote = 'gh'
    ```

9.  **Context Files (`g:context_files`)**:
    In addition to all files currently open in Vim buffers, you can specify a list of files to always include as context for AI commands like `:ViminiCode`. This is useful for providing the AI with important project files (like configurations, core utilities, or type definitions) without needing to have them open. Note: The total size of all uploaded context files combined is strictly limited to 1MB; if this limit is exceeded, the largest files will automatically be excluded.
    ```vim
    " Always include these files as context for code generation tasks
    let g:context_files = ['package.json', 'src/main.js', 'src/utils/api.js']
    ```

10.  **Project Configuration (`ViminiConfig`)**:
    Configure per-project settings such as whether compilation is needed, shell commands for compiling/building code and running tests when invoked by chat tools (`build_code` and `test_code`), and the directory path where temporary git worktrees can be created. Project configuration is stored in `~/.var/vimini/projects/<project_name>`:
    ```json
    {
      "version": "0.1",
      "configuration": {
        "build-command": "make -j$(nproc)",
        "test-command": "pytest",
        "compilation-needed": false,
        "worktree": "~/.var/vimini/worktree"
      },
      "files": []
    }
    ```

11. **Global & Backend Configuration (`~/.var/vimini/config.json`)**:
    Vimini manages global settings and model provider backends in `~/.var/vimini/config.json`. If this file does not exist, Vimini automatically generates a default configuration on startup:
    ```json
    {
      "default_backend": "Gemini",
      "backends": {
        "Gemini": {
          "api": "genai",
          "api_key_file": "~/.config/gemini.token",
          "default_model": "gemini-3.8-flash"
        }
      },
      "ui": {
        "thinking_color": "DarkGray",
        "prompt_color": "DarkBlue",
        "waiting_color": "Green",
        "service_color": "Green",
        "error_color": "Red",
        "prompt_hint_color": "Green"
      }
    }
    ```

    You can configure multiple backends (Google Gemini via `"api": "genai"`, OpenAI-compatible proxies and endpoints via `"api": "openai"`, or Anthropic via `"api": "anthropic"`).

    Backends that require credentials specify an `api_key_file`; Gemini requires
    one, while OpenAI-compatible local/proxy endpoints may omit it unless
    `requires_api_key` is set. The background agent always reads API keys from
    this global configuration file and never receives API key paths through
    runtime setup messages. You can also edit this file interactively from Vim with `:ViminiGlobalConfig`.

### `:ViminiGlobalConfig`

Opens an interactive configuration editor in a new split window to configure global Vimini settings and model provider backends stored in `~/.var/vimini/config.json`.

**How to use the global configuration manager:**
*   Move the cursor to any configuration option or backend line.
*   **`<Enter>` or `e`**: Edits the selected value. On `default_backend`, enter one of the configured backend names. On a backend header, rename the backend. Under `UI PREFERENCES`, customize colors (e.g. `thinking_color`, `prompt_color`, `waiting_color`, `service_color`, `error_color`, `prompt_hint_color`).
*   **`a`**: Adds a new backend and prompts for API type, default model, API key file, and base URL for OpenAI-compatible or Anthropic backends.
*   **`d`**: Clears the selected backend field, deletes a backend after confirmation, or resets a UI preference back to default. The last backend cannot be deleted.
*   **`r`**: Resets all global configuration options to their last saved state.
*   **`q`**: Closes the configuration window. If modifications were made, a confirmation prompt asks whether to save changes permanently to `~/.var/vimini/config.json`.

```vim
:ViminiGlobalConfig
```

12. **Active Backend (`g:vimini_backend`)**:
    Specifies the active backend name matching a key in `~/.var/vimini/config.json`. The default is `'Gemini'`.
    You can switch backends at runtime using the `:ViminiBackend` command.
    ```vim
    let g:vimini_backend = 'Gemini'
    ```

## Usage

### `:ViminiBackend [backend_name]`

Displays or switches the active model provider backend at runtime without restarting Vim:
*   **Without arguments**: Echoes the active backend and lists other available backends in parentheses (e.g. `Gemini (available: Custom 1, Custom 2)`).
*   **With argument**: Validates and switches to the specified backend, updating `g:vimini_backend` and `g:vimini_model` to that backend's default model, and sends an updated setup message to the background agent. Supports tab completion.

```vim
" Check current backend
:ViminiBackend

" Switch to an alternative backend
:ViminiBackend Custom 1
```

Vimini exposes several commands for interacting with the Gemini API:

### `:ViminiListModels`

Lists all available Gemini models in a new split window. This is
useful for viewing models available to the active backend.
Press `<Enter>` on a supported model to switch to it for the current session.
Press `D` on a supported model to save it as the active backend's default model
in `~/.var/vimini/config.json` and switch to it immediately.

```vim
:ViminiListModels
```

### `:ViminiChat`

Opens the chat buffer for interactive mode with Gemini.

```vim
:ViminiChat
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

Takes the content of all open buffers (and any files specified in `g:context_files`, up to a total of 1MB) as context, along with your
`prompt`, and asks the Gemini model to generate code. The result is
streamed into a new buffer named `[{job_id}] Vimini Code`. This buffer contains:
*   The AI's internal thought process as it works (if `g:vimini_thinking` is `on`).
*   A diff view showing the proposed changes across one or more files.

This command is ideal for asking the AI to refactor, debug, or extend
your current code.

```vim
:ViminiCode Please refactor this function to be more concise
```

After running `:ViminiCode`, you can use the following command to
apply the changes:

#### `:ViminiApply [job_id]`
*   `:ViminiApply [job_id]`: Applies the AI-generated changes to the relevant files on disk and reloads them in Vim if they are open. If you have multiple `Vimini Code` buffers open, you can specify the `job_id` to apply a specific one.

This command will close the temporary `Vimini Code` buffer.

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

### `:ViminiConfig`

Opens an interactive configuration editor in a new split window to configure project-specific settings (such as `build-command`, `test-command`, and `worktree`).

**How to use the configuration manager:**
*   Move the cursor to any configuration option line.
*   **`<Enter>` or `e`**: Opens an input prompt pre-filled with the current value to edit the option.
*   **`d`**: Clears/unsets the option under the cursor.
*   **`r`**: Resets all configuration options to their last saved state.
*   **`q`**: Closes the configuration window. If modifications were made, a confirmation popup prompts to save changes permanently to `~/.var/vimini/projects/<project_name>`.

```vim
:ViminiConfig
```

### `:ViminiReview [-c <git_objects>] [--pr <number>] [--mr <number>] [--remote <remote>] [--security] [--save[=<path>]] [{prompt}]`

Sends content to the Gemini model for a code review. This command has three main modes:

1.  **Current Buffer Review**: If no `-c`, `--pr`, or `--mr` option is provided, it sends the content of the current buffer for review.
2.  **Git Object Review**: If the `-c <git_objects>` option is provided, it reviews the changes specified by the git objects. It sends the `git show` output (the diff) to the AI, and critically, it also uploads the full content of all changed files to provide complete context for the review. `<git_objects>` can be any valid git object reference, like a commit hash, branch name, or a range like `HEAD~3..HEAD`.
3.  **GitHub PR / GitLab MR Review**: Use `--pr <number>` (or `--github <number>`) or `--mr <number>` (or `--gitlab <number>`) to automatically fetch the branch from the remote repository and run the review on its commits. You can optionally specify `--remote <remote>` (defaults to `g:vimini_default_remote`, `upstream`, the canonical remote for the current branch, or `origin`).

You can add an optional `{prompt}` to guide the AI's review. The review will be displayed in a new vertical split buffer. If `g:vimini_thinking` is `on`, an additional buffer showing the AI's thought process will also be opened.

**Additional Options:**

*   `--pr <number>` / `--github <number>`: Fetches and reviews the specified GitHub Pull Request number.
*   `--mr <number>` / `--gitlab <number>`: Fetches and reviews the specified GitLab Merge Request number.
*   `--remote <remote>`: Remote git repository to fetch PR/MR from (defaults to `g:vimini_default_remote`, `upstream`, the canonical remote for the current branch, or `origin`).
*   `--security`: Narrows the scope of the review to focus exclusively on security vulnerabilities, insecure coding practices, and potential attack vectors.
*   `--save[=<path>]`: Used with `-c`, `--pr`, or `--mr`. This option reviews each commit in the given range individually and saves each review to a separate file. If a path is provided (e.g., `--save=./reviews`), files are saved there. Otherwise, they are saved in the root of the git repository (e.g., `0001-fix-login-bug.review.txt`).

> **Note on the reviewed commit range:** Only the commits that belong to the
> PR/MR are reviewed. Vimini first refreshes the selected remote (a plain
> `git fetch`, without tags) so that the remote-tracking base branch is current,
> then determines which branch the PR/MR was opened against without contacting
> GitHub or GitLab: every remote-tracking branch of that remote is compared with
> the pulled head and the branch with the fewest missing commits is chosen, with
> the remote's default branch breaking ties. This also handles PRs/MRs that
> target a non-default branch. If no base branch can be determined, or it shares
> no history with the PR/MR, Vimini reports a warning and falls back to reviewing
> only the tip commit. The resolved base and commit count are echoed before the
> review starts.

> **Note on Worktrees:** When reviewing PRs or MRs, Vimini creates an isolated temporary git worktree so the review agent can inspect post-change files without altering your working copy or interfering with unstaged changes. Worktrees are only created if the `worktree` option is set in `:ViminiConfig` (e.g., `~/.var/vimini/worktree`). If not set (default), worktrees are not allowed and Vimini falls back to your local repository directory.

**Examples:**

```vim
" Review the current buffer for performance issues
:ViminiReview Check for performance issues.

" Perform a general review of the current buffer
:ViminiReview

" Review the changes in the latest commit
:ViminiReview -c HEAD

" Review changes from two commits ago, focusing only on security
:ViminiReview -c HEAD~2 --security

" Fetch and review GitHub Pull Request #123
:ViminiReview --pr 123

" Fetch and review GitLab Merge Request #456 from remote upstream
:ViminiReview --mr 456 --remote upstream

" Review GitHub PR #123, focusing on security
:ViminiReview --pr 123 --security

" Review GitHub PR #123 and save each commit review to a file
:ViminiReview --pr 123 --save=./reviews
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

#### `:ViminiCommit [-n] [-r] [-a] [instructions]`

Automates the commit process using AI. This command has three main modes:

**Default Mode (Creating a new commit):**

When run without flags (or with `-n`), it automates the creation of a new commit:
1.  Stages all current changes (`git add .`).
2.  Generates a commit message based on the staged diff.
3.  Opens a commit message buffer for editing. The buffer displays the diff stat with all files included in the commit as comments at the bottom.
4.  In Normal mode, move the cursor over the diff stat file line and press `<Space>` to toggle file inclusion (deselected files are marked with `#-`, and pressing `<Space>` again re-adds them) to exclude the file.
5.  Save and close the buffer to commit. All deselected files are removed from the commit, and only the remaining files are committed. Close without saving to abort.

**Regenerate Mode (`-r`):**

When run with the `-r` flag, it regenerates the commit message for the last commit (`HEAD`) and amends it:
1.  It gets the diff from the `HEAD` commit.
2.  It generates a new commit message based on that diff.
3.  Opens the commit message buffer listing the diff stat for `HEAD` (file toggling is not available in regenerate mode).
4.  If confirmed and saved, it amends the `HEAD` commit with the new message.

**Amend Code and Regenerate Mode (`-a`):**

When run with the `-a` flag, it amends the current working tree changes into the last commit (`HEAD`) and regenerates the commit message:
1.  Stages current changes.
2.  Amends the code into `HEAD` (`git commit --amend --no-edit`).
3.  Generates a new commit message based on the new combined diff from `HEAD`.
4.  Opens the commit message buffer listing the combined diff stat (press `<Space>` in Normal mode to toggle/deselect files with `#-`).
5.  If confirmed and saved, it amends the `HEAD` commit with the new message and deselected files removed.

You can also provide optional `[instructions]` at the end of the command to guide the AI's message generation (e.g., "Mention issue #123" or "Keep it very brief").

**Options:**

*   **`-n`**: Omit the `Co-authored-by` trailer for a specific commit. By default, a trailer is appended and can be configured with `g:vimini_commit_author`.
*   **`-r`**: Enable the regenerate message mode for `HEAD`.
*   **`-a`**: Amend current changes into `HEAD` and regenerate the commit message.

**Examples:**
```vim
" Stage changes and generate a commit message for a new commit
:ViminiCommit

" Provide instructions for the commit message
:ViminiCommit Include details about the bug fix

" Do the same, but without the co-author trailer
:ViminiCommit -n

" Regenerate the message for the HEAD commit and amend it
:ViminiCommit -r

" Regenerate with instructions
:ViminiCommit -r Make it shorter

" Amend current changes into HEAD and regenerate commit message
:ViminiCommit -a
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

Commands like `:ViminiCode` automatically upload files to Google to provide
context for the AI. These files persist on the server. `:ViminiFiles` opens an
interactive buffer to manage these remotely stored files, allowing you to view
details or delete them when they are no longer needed.

The command opens a `Vimini Files` buffer that lists all your uploaded files.

**How to use the manager:**
*   **`i`**: Shows detailed information about the file under the cursor in a new window.
*   **`d`**: Deletes the file under the cursor from the server. The list is refreshed automatically.
*   **`D`**: Deletes all uploaded files from the server (with confirmation).
*   **`q`**: Closes the `Vimini Files` buffer.

```vim
:ViminiFiles
```

### `:ViminiStatus`

Opens a read-only buffer showing the status of currently running asynchronous jobs (e.g., code generation, chat, review). This is useful for monitoring long-running tasks.

```vim
:ViminiStatus
```

### `:ViminiReload`

Reloads the Vimini Python modules from disk. This is primarily useful for developers working on the plugin, allowing them to test changes and achieve faster development iterations without having to restart Vim.

```vim
:ViminiReload
```

### `:ViminiHelp [command]`

Opens a help buffer listing all available Vimini commands and their descriptions. You can optionally provide a command name to jump directly to its help entry.

```vim
:ViminiHelp
:ViminiHelp ViminiCode
```

## Contributing

Contributions are welcome! Please check [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines on development setup, code formatting with `ruff`, and testing.

## Shepherd's note
Most of the code here is generated by Gemini itself, I only provide
guidance and occasionally edit some small bit where it is easier than
asking

Also, it's important to remember that AI, much like a well-fed cat,
requires a steady stream of attention and high-quality prompts to
perform its best tricks. Neglect it, and you might just find it
napping on your keyboard when you need it most.

--
Simo.
