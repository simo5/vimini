" plugin/vimini.vim
" Main entry point for vimini

" Prevent the script from being loaded more than once.
if exists('g:loaded_vimini')
  finish
endif
let g:loaded_vimini = 1

" Default configuration
" Configuration: API Key
" Priority:
" 1. g:vimini_api_key
" 2. ~/.vimini/api_key file
let s:api_key = get(g:, 'vimini_api_key', '')
if empty(s:api_key)
  let s:api_key_path = expand('~/.config/gemini.token')
  if filereadable(s:api_key_path)
    try
      let s:api_key = trim(readfile(s:api_key_path)[0])
    catch
      " Handle empty file case
      let s:api_key = ''
    endtry
  endif
endif

" Configuration: Model name
let g:vimini_model = get(g:, 'vimini_model', 'gemini-2.5-flash')

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
    python_root_dir = normpath(join(plugin_root_dir, '..', 'python3'))
    sys.path.insert(0, python_root_dir)

    from vimini import main
    api_key = vim.eval('s:api_key')
    model = vim.eval('g:vimini_model')
    main.initialize(api_key=api_key, model=model)
except Exception as e:
    # Escape single quotes in the error message to prevent Vimscript errors
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF

" Expose a function to list available models
function! ViminiListModels()
  py3 << EOF
try:
    from vimini import main
    main.list_models()
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF
endfunction

command! ViminiListModels call ViminiListModels()

" Expose a function to Chat with Gemini
function! ViminiChat(prompt)
  py3 << EOF
try:
    from vimini import main
    prompt = vim.eval('a:prompt')
    main.chat(prompt)
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF
endfunction

command! -nargs=* ViminiChat call ViminiChat(string(<q-args>))

" Expose a function to generate code with Gemini
function! ViminiCode(prompt)
  py3 << EOF
try:
    from vimini import main
    prompt = vim.eval('a:prompt')
    main.code(prompt)
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF
endfunction

command! -nargs=* ViminiCode call ViminiCode(string(<q-args>))

" Expose a function to apply the generated code
function! ViminiApply(...)
  let l:option = get(a:, 1, '')
  py3 << EOF
try:
    from vimini import main
    option = vim.eval('l:option')
    if option == 'append':
        main.append_code()
    else:
        main.apply_code()
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF
endfunction

command! -nargs=? ViminiApply call ViminiApply(<f-args>)

function! ViminiReview(prompt)
  py3 << EOF
try:
    from vimini import main
    prompt = vim.eval('a:prompt')
    main.review(prompt)
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF
endfunction

command! -nargs=* ViminiReview call ViminiReview(string(<q-args>))

" Expose a function to show git diff
function! ViminiDiff()
  py3 << EOF
try:
    from vimini import main
    main.show_diff()
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF
endfunction

command! ViminiDiff call ViminiDiff()

" Expose a function to generate and execute a git commit
function! ViminiCommit()
  py3 << EOF
try:
    from vimini import main
    main.commit()
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF
endfunction

command! ViminiCommit call ViminiCommit()
