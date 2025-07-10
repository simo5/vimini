import vim

def hello():
    """
    A simple function that prints a message in Vim.
    This is the main logic of our plugin.
    """
    # The g:vimini_user_name variable is guaranteed to exist by the
    # plugin/vimini.vim script, which sets a default value.
    user_name = vim.eval('g:vimini_user_name')

    # Use vim.command to execute an Ex command.
    # f-strings are great for this.
    vim.command(f'echo "Hello, {user_name}! This is from Python."')

    # You can also interact with the buffer, window, etc.
    # For example, to add a line to the current buffer:
    # vim.current.buffer.append("This line was added by a Python script.")
