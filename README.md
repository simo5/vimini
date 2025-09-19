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
    use 'your-github-username/vimini.nvim' " Replace with the actual repo path
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

5.  **Commit Author (`g:vimini_commit_author`)**:
    Customize the `Co-authored-by` trailer used in `:ViminiCommit`.
    ```vim
    " Set a custom author trailer (Default is 'Co-authored-by: Gemini <gemini@google.com>')
    let g:vimini_commit_author = 'Co-authored-by: My AI Assistant <ai@example.com>'
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

### `:ViminiCode {prompt}`

Takes the content of all open buffers as context, along with your
`prompt`, and asks the Gemini model to generate code. The result is
streamed into several new buffers:
*   `Vimini Code`: The generated code.
*   `Vimini Diff`: A diff view comparing the original code with the AI's suggestion.
*   `Vimini Thoughts` (Optional): If `g:vimini_thinking` is `on`, this buffer shows the AI's internal monologue as it works.

This command is ideal for asking the AI to refactor, debug, or extend
your current code.

```vim
:ViminiCode Please refactor this function to be more concise
```

After running `:ViminiCode`, you can use one of the following commands to
apply the changes:

#### `:ViminiApply [append]`
*   `:ViminiApply`: Replaces the entire content of your original buffer
    with the AI-generated code.
*   `:ViminiApply append`: Appends the AI-generated code to the end of
    your original buffer.

Both commands will close the temporary `Vimini Code`, `Vimini Diff`, and
`Vimini Thoughts` buffers.

### `:ViminiReview [C:<git_objects>] [{prompt}]`

Sends content to the Gemini model for a code review. This command has two modes:

1.  **Current Buffer Review**: If no `git_objects` are provided, it sends the content of the current buffer for review.
2.  **Git Object Review**: If the command starts with `C:<git_objects>`, it reviews the output of `git show <git_objects>`. `<git_objects>` can be any valid git object reference, like a commit hash, branch name, or `HEAD~1`.

You can add an optional `{prompt}` to guide the AI's review. The review will be displayed in a new vertical split buffer. If `g:vimini_thinking` is `on`, an additional buffer showing the AI's thought process will also be opened.

**Examples:**

```vim
" Review the current buffer for performance issues
:ViminiReview Check for performance issues.

" Perform a general review of the current buffer
:ViminiReview

" Review the changes in the latest commit
:ViminiReview C:HEAD

" Review changes from two commits ago and ask for security vulnerabilities
:ViminiReview C:HEAD~2 "Check for security vulnerabilities"
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

#### `:ViminiCommit [-n]`

Automates the commit process using AI. This command:
1.  Stages all current changes (`git add .`).
2.  Generates a conventional commit message (subject and body) based on
    the staged diff.
3.  Displays the generated message in a popup for you to confirm (`y`)
    or cancel (`n`).
4.  If confirmed, it commits the changes with the generated message.
5.  By default, it appends a `Co-authored-by` trailer, which can be
    configured with `g:vimini_commit_author`.

You can use the optional `-n` flag to omit the author trailer for a
specific commit.

```vim
" Generate a commit message with the co-author trailer
:ViminiCommit

" Generate a commit message without the co-author trailer
:ViminiCommit -n
```

## Sheperd's note
Most of the code here is generated by Gemini itself, I only provide
guidance and occasioanly edit some small bit where it is easier then
asking

Also, it's important to remember that AI, much like a well-fed cat,
requires a steady stream of attention and high-quality prompts to
perform its best tricks. Neglect it, and you might just find it
napping on your keyboard when you need it most.
-- Simo.
