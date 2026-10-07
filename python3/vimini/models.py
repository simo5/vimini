import vim
from . import util

_VIMINI_MODELS = []
_LINE_TO_MODEL = {}

SUPPORTED_CATEGORY = "Multimodal & Text Generation"

CATEGORY_ORDER = [
    SUPPORTED_CATEGORY,
    "Image Generation",
    "Audio Generation",
    "Video Generation",
    "Embeddings",
    "Other / Specialized",
]


def categorize_model(model):
    """
    Categorizes a model based on its supported actions, description, and metadata.
    """
    if isinstance(model, str):
        name = model
        display_name = ""
        description = ""
        actions = []
    else:
        name = model.get("name", "")
        display_name = model.get("display_name", "") or ""
        description = model.get("description", "") or ""
        actions = (
            model.get("supported_actions", [])
            or model.get("supported_generation_methods", [])
            or []
        )

    if name.startswith("models/"):
        name = name[7:]

    name_lower = name.lower()
    display_lower = display_name.lower()
    desc_lower = description.lower()
    actions_lower = [str(a).lower() for a in actions]

    combined_text = f"{name_lower} {display_lower} {desc_lower}"

    # 0. Specialized / Agent checks
    # Any model with bidiGenerateContent in actions, or "agent" in name or description
    if (
        any("bidigeneratecontent" in a for a in actions_lower)
        or "agent" in name_lower
        or "agent" in desc_lower
    ):
        return "Other / Specialized"

    # 1. Embeddings
    if (
        (
            any("embed" in a for a in actions_lower)
            and not any("generatecontent" in a for a in actions_lower)
        )
        or "embedding" in name_lower
        or "embedding" in display_lower
    ):
        return "Embeddings"

    # 2. Image Generation
    if (
        any("generateimages" in a or "imagegeneration" in a for a in actions_lower)
        or "image generation" in desc_lower
        or "generate image" in desc_lower
        or "generates images" in desc_lower
        or "text-to-image" in desc_lower
        or "text to image" in desc_lower
        or name_lower.startswith("imagen")
        or "banana" in name_lower
        or "banana" in display_lower
    ):
        return "Image Generation"

    # 3. Video Generation
    if (
        any("generatevideos" in a or "videogeneration" in a for a in actions_lower)
        or "video generation" in desc_lower
        or "generate video" in desc_lower
        or "text-to-video" in desc_lower
        or "text to video" in desc_lower
        or name_lower.startswith("veo")
        or "veo" in name_lower
    ):
        return "Video Generation"

    # 4. Audio & Speech Generation / Transcription
    if (
        any(k in desc_lower for k in ["audio", "music", "tts", "transcribe"])
        or any(
            k in desc_lower
            for k in [
                "audio generation",
                "generate audio",
                "music generation",
                "speech generation",
                "text-to-speech",
                "text to speech",
                "speech-to-text",
                "speech to text",
                "transcription",
                "transcribe",
            ]
        )
        or any(
            k in name_lower or k in display_lower
            for k in [
                "chirp",
                "tts",
                "speech",
                "sound",
                "lyria",
                "transcribe",
                "transcription",
            ]
        )
        or ("audio" in name_lower and not name_lower.startswith("gemini-"))
    ):
        return "Audio Generation"

    # 5. Robotics / Embodied AI (placed under Other / Specialized)
    if any(k in combined_text for k in ["robotics", "robot", "embodied"]):
        return "Other / Specialized"

    # 6. Multimodal & Text Generation
    if (
        any("generatecontent" in a for a in actions_lower)
        or name_lower.startswith("gemini")
        or name_lower.startswith("gemma")
        or name_lower.startswith("learnlm")
    ):
        return SUPPORTED_CATEGORY

    return "Other / Specialized"


def build_models_buffer(models, current_model):
    """
    Builds the buffer lines and line-to-model mapping grouped by category.
    Returns (buffer_lines, line_map, current_model_line).
    """
    buffer_lines = [
        "| Vimini Available Models",
        "|----------------------------------------------------------------------",
        "| <CR>: switch model | q: close",
        "| Note: Only 'Multimodal & Text Generation' models are currently supported.",
        "",
    ]
    line_map = {}
    current_model_line = None

    if current_model and current_model.startswith("models/"):
        current_model = current_model[7:]

    from collections import defaultdict

    grouped = defaultdict(list)
    for m in models:
        cat = categorize_model(m)
        grouped[cat].append(m)

    known_cats = [c for c in CATEGORY_ORDER if c in grouped]
    other_cats = sorted([c for c in grouped if c not in CATEGORY_ORDER])
    all_cats = known_cats + other_cats

    for cat in all_cats:
        cat_models = grouped[cat]
        if not cat_models:
            continue

        buffer_lines.append(f"[{cat}]")
        line_map[len(buffer_lines)] = None

        for m in cat_models:
            if isinstance(m, str):
                name = m
                display = m
            else:
                name = m.get("name", "")
                display = m.get("display_name", name)

            if name.startswith("models/"):
                name = name[7:]

            is_current = name == current_model
            prefix = " * " if is_current else "   "
            buffer_lines.append(f"{prefix}{display}")
            line_num = len(buffer_lines)
            line_map[line_num] = {
                "model": m,
                "name": name,
                "display": display,
                "category": cat,
            }
            if is_current and current_model_line is None:
                current_model_line = line_num

        buffer_lines.append("")
        line_map[len(buffer_lines)] = None

    return buffer_lines, line_map, current_model_line


def show_models_list(models):
    global _VIMINI_MODELS, _LINE_TO_MODEL
    _VIMINI_MODELS = models

    buffer_lines, _LINE_TO_MODEL, current_model_line = build_models_buffer(
        models, util._MODEL
    )

    buf_name = "ViminiModels"
    try:
        win_nr = int(vim.eval(f"bufwinnr('^{buf_name}$')"))
    except Exception:
        win_nr = -1

    if win_nr > 0:
        vim.command(f"{win_nr}wincmd w")
    else:
        try:
            buf_nr = int(vim.eval(f"bufnr('^{buf_name}$')"))
            if buf_nr > 0:
                vim.command(f"silent! bdelete! {buf_nr}")
        except Exception:
            pass
        util.new_split()
        vim.command(f"file {buf_name}")

    buf = vim.current.buffer
    vim.command("setlocal modifiable")
    buf[:] = buffer_lines
    vim.command("setlocal buftype=nofile noswapfile bufhidden=wipe nomodifiable")

    vim.command("syntax match ViminiModelCurrent '^\\s*\\*\\s*\\zs.*$'")
    vim.command("syntax match ViminiModelHeader '^|.*'")
    vim.command("syntax match ViminiModelCategory '^\\[.*\\]$'")
    vim.command("highlight default link ViminiModelCurrent String")
    vim.command("highlight default link ViminiModelHeader Comment")
    vim.command("highlight default link ViminiModelCategory Title")

    vim.command(
        "nnoremap <buffer> <silent> <CR> :py3 from vimini.models import select_model; select_model()<CR>"
    )
    vim.command("nnoremap <buffer> <silent> q :q<CR>")

    try:
        if current_model_line is not None:
            vim.current.window.cursor = (current_model_line, 3)
        else:
            first_selectable = next(
                (ln for ln, item in _LINE_TO_MODEL.items() if item is not None), None
            )
            if first_selectable:
                vim.current.window.cursor = (first_selectable, 3)
            else:
                vim.current.window.cursor = (1, 1)
    except vim.error:
        pass


def select_model():
    global _LINE_TO_MODEL
    try:
        buf = vim.current.buffer
        win = vim.current.window
        line_num, col = win.cursor

        entry = _LINE_TO_MODEL.get(line_num)
        if not entry:
            return

        cat = entry.get("category")
        if cat != SUPPORTED_CATEGORY:
            util.display_message(f"Category '{cat}' is unsupported", error=True)
            return

        model_name = entry.get("name", "")
        if model_name.startswith("models/"):
            model_name = model_name[7:]

        util._MODEL = model_name
        util._MODEL_NAME = None
        vim.command(f"let g:vimini_model = '{model_name}'")

        from vimini import main

        main.send_setup()

        vim.command("setlocal modifiable")
        buffer_lines, _LINE_TO_MODEL, _ = build_models_buffer(
            _VIMINI_MODELS, util._MODEL
        )
        buf[:] = buffer_lines
        vim.command("setlocal nomodifiable")
        vim.command("redraw")

        util.display_message(f"Switched model to {model_name}")

    except Exception as e:
        util.display_message(f"Error selecting model: {e}", error=True)
