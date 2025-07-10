" plugin/vimini.vim
" Main entry point for vimini

" Prevent the script from being loaded more than once.
if exists('g:loaded_vimini')
  finish
endif
let g:loaded_vimini = 1

" Default configuration
let g:vimini_user_name = get(g:, 'vimini_user_name', 'Vim User')

let s:plugin_root_dir = fnamemodify(resolve(expand('<sfile>:p')), ':h')

py3 << EOF
from os.path import normpath, join
import vim
try:
    # It's good practice to add the plugin's python directory to sys.path
    # to ensure imports work correctly, especially for larger projects.
    import sys
    import os
    plugin_root_dir = vim.eval('s:plugin_root_dir')
    python_root_dir = normpath(join(plugin_root_dir, '..', 'python'))
    sys.path.insert(0, python_root_dir)

    from vimini import main
    main.hello()
except Exception as e:
    vim.command(f"echoerr '[MyPlugin] Error: {e}'")
EOF
