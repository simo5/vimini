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

" Configuration: Log file
let g:vimini_log_file = get(g:, 'vimini_log_file', expand('~/.var/vimini/vimini.log'))
" Configuration: Logging on/off
let g:vimini_logging = get(g:, 'vimini_logging', 'off')

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
    log_file = vim.eval('g:vimini_log_file') if vim.eval('g:vimini_logging') == 'on' else None
    main.initialize(api_key=api_key, model=model, logfile=log_file)
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

let g:vimini_thinking = get(g:, 'vimini_thinking', 'on')

" Expose a function to toggle thinking on and off
function! ViminiThinking(...)
  let l:option = get(a:, 1, '')

  " Handle explicit setting
  if !empty(l:option)
    if l:option ==# 'on' || l:option ==# 'off'
      let g:vimini_thinking = l:option
    else
      echoerr "[Vimini] Invalid argument for ViminiThinking. Use 'on' or 'off'."
      return
    endif
  " Handle toggling
  else
    if g:vimini_thinking ==# 'on'
      let g:vimini_thinking = 'off'
    else
      let g:vimini_thinking = 'on'
    endif
  endif

  echo "[Vimini] Thinking is now " . g:vimini_thinking
endfunction

command! -nargs=? ViminiThinking call ViminiThinking(<f-args>)

" Expose a function to toggle logging on and off
function! ViminiToggleLogging(...)
  let l:option = get(a:, 1, '')

  " Handle explicit setting
  if !empty(l:option)
    if l:option ==# 'on' || l:option ==# 'off'
      let g:vimini_logging = l:option
    else
      echoerr "[Vimini] Invalid argument for ViminiToggleLogging. Use 'on' or 'off'."
      return
    endif
  " Handle toggling
  else
    if g:vimini_logging ==# 'on'
      let g:vimini_logging = 'off'
    else
      let g:vimini_logging = 'on'
    endif
  endif

  " Call Python function to update logging state.
  py3 << EOF
try:
    from vimini import main
    log_state = vim.eval('g:vimini_logging')
    if log_state == 'on':
        log_file = vim.eval('g:vimini_log_file')
        main.logging(log_file)
    else:
        main.logging()
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error setting log state: {error_message}'")
EOF

  echo "[Vimini] Logging is now " . g:vimini_logging
endfunction

command! -nargs=? ViminiToggleLogging call ViminiToggleLogging(<f-args>)

" Expose a function to generate code with Gemini
function! ViminiCode(prompt)
  py3 << EOF
try:
    from vimini import main
    prompt = vim.eval('a:prompt')
    verbose = vim.eval('g:vimini_thinking') == 'on'
    main.code(prompt, verbose)
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF
endfunction

command! -nargs=* ViminiCode call ViminiCode(string(<q-args>))

" Expose a function to apply the generated code
function! ViminiApply()
  py3 << EOF
try:
    from vimini import main
    main.apply_code()
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF
endfunction

command! -nargs=0 ViminiApply call ViminiApply()

function! ViminiReview(q_args)
  " Reviews git diffs. The first argument can be a git object if prefixed
  " with "C:". The rest of the arguments are treated as a prompt.
  " If no "C:" argument is found, all arguments form the prompt.
  let l:git_objects_arg = v:null
  let l:prompt_arg = ''
  let l:args = split(a:q_args) " Parse the string from <q-args> into arguments

  if !empty(l:args)
    let l:first_arg = l:args[0]
    " Check if the first argument is a git object reference
    if strpart(l:first_arg, 0, 2) ==# 'C:'
      let l:git_objects_arg = strpart(l:first_arg, 2)
      " The rest of the arguments form the prompt
      if len(l:args) > 1
        let l:prompt_arg = join(l:args[1:], ' ')
      endif
    else
      " All arguments form the prompt
      let l:prompt_arg = join(l:args, ' ')
    endif
  endif

  py3 << EOF
try:
    from vimini import main
    prompt = vim.eval('l:prompt_arg')
    git_objects = vim.eval('l:git_objects_arg')
    verbose = vim.eval('g:vimini_thinking') == 'on'
    main.review(prompt, git_objects=git_objects, verbose=verbose)
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF
endfunction

command! -nargs=* ViminiReview call ViminiReview(<q-args>)

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

" Configuration: Commit author trailer
let g:vimini_commit_author = get(g:, 'vimini_commit_author', 'Co-authored-by: Gemini <gemini@google.com>')

" Expose a function to generate and execute a git commit
function! ViminiCommit(...)
  let l:author = g:vimini_commit_author
  if a:0 > 0 && a:1 ==# '-n'
    let l:author = v:null
  endif

  py3 << EOF
try:
    from vimini import main
    author = vim.eval('l:author')
    main.commit(author=author)
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF
endfunction

command! -nargs=? ViminiCommit call ViminiCommit(<f-args>)


" Expose a function for autocompletion.
" This calls the non-blocking python function that handles the async request.
function! ViminiAutocomplete()
  py3 << EOF
try:
    from vimini import main
    verbose = vim.eval('g:vimini_thinking') == 'on'
    main.autocomplete(verbose)
except Exception as e:
    error_message = str(e).replace("'", "''")
    vim.command(f"echoerr '[Vimini] Error: {error_message}'")
EOF
endfunction

" Configuration: Autocomplete on/off
let g:vimini_autocomplete = get(g:, 'vimini_autocomplete', 'off')

let s:autocomplete_timer = -1
let s:autocomplete_queue_timer = -1

" Function to be called by the queue timer to process results from python
function! s:ProcessAutocompleteQueue(timer)
  py3 << EOF
try:
    from vimini import main
    main.process_autocomplete_queue()
except Exception:
    # Fail silently, this is called frequently
    pass
EOF
endfunction

" Expose a function to toggle autocomplete on and off
function! ViminiToggleAutocomplete(...)
  let l:option = get(a:, 1, '')
  let l:old_state = g:vimini_autocomplete

  " Handle explicit setting
  if !empty(l:option)
    if l:option ==# 'on' || l:option ==# 'off'
      let g:vimini_autocomplete = l:option
    else
      echoerr "[Vimini] Invalid argument for ViminiToggleAutocomplete. Use 'on' or 'off'."
      return
    endif
  " Handle toggling
  else
    if g:vimini_autocomplete ==# 'on'
      let g:vimini_autocomplete = 'off'
    else
      let g:vimini_autocomplete = 'on'
    endif
  endif

  if g:vimini_autocomplete ==# 'on'
    " Start the queue processor timer if it's not already running
    if s:autocomplete_queue_timer == -1
      let s:autocomplete_queue_timer = timer_start(100, 's:ProcessAutocompleteQueue', {'repeat': -1})
    endif
  else " 'off'
    call s:StopAutocompleteTimer() " Stops trigger timer and cancels jobs
    " Also stop the queue processor timer
    if s:autocomplete_queue_timer != -1
      call timer_stop(s:autocomplete_queue_timer)
      let s:autocomplete_queue_timer = -1
    endif
  endif

  echo "[Vimini] Autocomplete is now " . g:vimini_autocomplete
endfunction

command! -nargs=? ViminiToggleAutocomplete call ViminiToggleAutocomplete(<f-args>)

function! s:CancelAutocomplete()
  py3 << EOF
try:
    from vimini import main
    # This signals the python background thread to stop and clears the queue.
    main.cancel_autocomplete()
except Exception:
    # Fail silently. It's not critical if this fails.
    pass
EOF
endfunction

function! s:StopAutocompleteTimer()
  if exists('s:autocomplete_timer') && s:autocomplete_timer != -1
    call timer_stop(s:autocomplete_timer)
    let s:autocomplete_timer = -1
  endif
  " Cancel any running python autocomplete job.
  call s:CancelAutocomplete()
endfunction

function! s:ResetAutocompleteTimer()
  " Stop the previous timer and cancel any pending request.
  call s:StopAutocompleteTimer()
  if g:vimini_autocomplete ==# 'on' && mode() ==# 'i'
    let s:autocomplete_timer = timer_start(1000, 's:TriggerAutocomplete')
  endif
endfunction

function! s:TriggerAutocomplete(timer)
  let s:autocomplete_timer = -1
  if g:vimini_autocomplete ==# 'on' && mode() ==# 'i'
    call ViminiAutocomplete()
  endif
endfunction

augroup vimini_autocomplete
  autocmd!
  autocmd TextChangedI * call s:ResetAutocompleteTimer()
  autocmd InsertLeave * call s:StopAutocompleteTimer()
augroup END