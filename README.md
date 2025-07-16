# Vimini: Google Gemini Integration for Vim

Vimini is a Vim plugin that provides seamless integration with Google's
Gemini (Generative AI) models, allowing you to interact with AI directly
from your Vim editor. You can chat, generate code, list available models,
and get code reviews without leaving your coding environment.

## Features

*   **Chat with Gemini**: Send prompts and receive responses in a new
    buffer.
*   **Code Generation**: Use the current buffer content as context to
    generate code.
*   **Code Review**: Get AI-powered reviews and suggestions for the code
    in your current buffer.
*   **Git Integration**: Generate commit messages and view diffs using
    AI.
*   **List Models**: Easily view all available Gemini models.
*   **API Key Management**: Configure your Gemini API key securely.

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

Vimini requires your Google Gemini API key and a default model to
function.

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
    Specify the default Gemini model you want to use for chat, code
    generation, and reviews. You can list available models using
    `:ViminiListModels`.
    ```vim
    let g:vimini_model = 'gemini-pro' " Or 'gemini-pro-vision', etc.
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

### `:ViminiCode {prompt}`

Takes the content of all open buffers as context, along with your
`prompt`, and asks the Gemini model to generate code. The generated code
is displayed in a new split buffer (`Vimini Code`), along with a third
buffer showing a diff against the original file (`Vimini Diff`).

This command is ideal for asking the AI to refactor, debug, or extend
your current code.

```vim
:ViminiCode Please refactor this function to be more concise
```

After running `:ViminiCode`, you can use one of the following commands to
apply the changes:

#### `:ViminiApply`
Replaces the entire content of your original buffer with the
AI-generated code from the `Vimini Code` buffer. It then closes the
temporary `Vimini Code` and `Vimini Diff` buffers.

#### `:ViminiApply append`
Appends the AI-generated code to the end of your original buffer. This
is useful when you've asked the AI to add a new function or class. It
also closes the temporary buffers.

### `:ViminiReview {prompt}`

Sends the content of the current buffer to the Gemini model for a code
review. The AI's review and suggestions will be displayed in a new
vertical split buffer. You can optionally add a `prompt` to guide the
review.

```vim
:ViminiReview Check for performance issues.
:ViminiReview " (No specific prompt, just a general review)
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

#### `:ViminiCommit`

Automates the commit process using AI. This command:
1.  Stages all current changes (`git add .`).
2.  Generates a conventional commit message (subject and body) based on
    the staged diff.
3.  Displays the generated message in a popup for you to confirm (`y`)
    or cancel (`n`).
4.  If confirmed, it commits the changes with the generated message and
    a `Co-authored-by: Gemini` trailer.

```vim
:ViminiCommit
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
