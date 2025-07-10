" plugin/vimini.vim
" Main entry point for vimini

" Prevent the script from being loaded more than once.
if exists('g:loaded_vimini')
  finish
endif
let g:loaded_vimini = 1

" Default configuration
let g:vimini_user_name = get(g:, 'vimini_user_name', 'Vim User')

" Expose the functionality to the user via a command.
