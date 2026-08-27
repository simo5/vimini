import vim
from . import util

_VIMINI_MODELS = []

def show_models_list(models):
    global _VIMINI_MODELS
    _VIMINI_MODELS = models

    buffer_lines = [
        "| Vimini Available Models",
        "|----------------------------------------------------------------------",
        "| <CR>: switch model | q: close",
        ""
    ]

    current_model = util._MODEL
    if current_model and current_model.startswith("models/"):
        current_model = current_model[7:]

    for m in models:
        if isinstance(m, str):
            name = m
            display = m
        else:
            name = m.get("name", "")
            display = m.get("display_name", name)

        if name.startswith("models/"):
            name = name[7:]

        prefix = " * " if name == current_model else "   "
        buffer_lines.append(f"{prefix}{display}")

    util.new_split()
    vim.command('file ViminiModels')
    buf = vim.current.buffer
    buf[:] = buffer_lines

    vim.command('setlocal buftype=nofile noswapfile nomodifiable')

    vim.command("syntax match ViminiModelCurrent '^\\s*\\*\\s*\\zs.*$'")
    vim.command("syntax match ViminiModelHeader '^|.*'")
    vim.command("highlight default link ViminiModelCurrent String")
    vim.command("highlight default link ViminiModelHeader Comment")

    vim.command("nnoremap <buffer> <silent> <CR> :py3 from vimini.models import select_model; select_model()<CR>")
    vim.command("nnoremap <buffer> <silent> q :q<CR>")

    try:
        idx = -1
        for i, m in enumerate(models):
            name = m if isinstance(m, str) else m.get("name", "")
            if name.startswith("models/"):
                name = name[7:]
            if name == current_model:
                idx = i
                break

        if idx != -1:
            vim.current.window.cursor = (idx + 5, 3)
        else:
            vim.current.window.cursor = (5, 3)
    except vim.error:
        pass

def select_model():
    try:
        buf = vim.current.buffer
        win = vim.current.window
        line_num, col = win.cursor
        line = buf[line_num - 1]

        if not line or line.startswith('|'):
            return

        idx = line_num - 5
        if idx < 0 or idx >= len(_VIMINI_MODELS):
            return

        m = _VIMINI_MODELS[idx]
        model_name = m if isinstance(m, str) else m.get("name", "")
        if model_name.startswith("models/"):
            model_name = model_name[7:]

        util._MODEL = model_name
        util._MODEL_NAME = None
        vim.command(f"let g:vimini_model = '{model_name}'")

        from vimini import main
        main.send_setup()

        vim.command('setlocal modifiable')
        current_model = util._MODEL
        if current_model and current_model.startswith("models/"):
            current_model = current_model[7:]
        buffer_lines = buf[:4]
        for m in _VIMINI_MODELS:
            if isinstance(m, str):
                name = m
                display = m
            else:
                name = m.get("name", "")
                display = m.get("display_name", name)

            if name.startswith("models/"):
                name = name[7:]

            prefix = " * " if name == current_model else "   "
            buffer_lines.append(f"{prefix}{display}")
        buf[:] = buffer_lines
        vim.command('setlocal nomodifiable')
        vim.command('redraw')

        util.display_message(f"Switched model to {model_name}")

    except Exception as e:
        util.display_message(f"Error selecting model: {e}", error=True)
