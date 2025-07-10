" autoload/vimini.vim
" Autoloaded functions for vimini

" This function is the main bridge from Vimscript to Python.
function! vimini#hello() abort
  py3 << EOF
import vim
try:
    # It's good practice to add the plugin's python directory to sys.path
    # to ensure imports work correctly, especially for larger projects.
    import sys
    import os
    script_path = vim.eval('s:script_path')
    plugin_root = os.path.dirname(os.path.dirname(script_path))
    python_dir = os.path.join(plugin_root, 'python3')
    if python_dir not in sys.path:
        sys.path.insert(0, python_dir)

    from vimini import main
    main.hello()
except Exception as e:
    vim.command(f"echoerr '[MyPlugin] Error: {e}'")
