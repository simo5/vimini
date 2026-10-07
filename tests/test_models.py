import sys
import os
from unittest.mock import MagicMock, patch

# Ensure python3 root is in sys.path
sys.path.insert(
    0, os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "python3"))
)

from vimini.models import (
    categorize_model,
    build_models_buffer,
    select_model,
    SUPPORTED_CATEGORY,
    _LINE_TO_MODEL,
)
import vimini.models as models_module
from vimini import util


def test_categorize_model():
    # Multimodal & Text Generation
    assert (
        categorize_model(
            {
                "name": "models/gemini-2.5-flash",
                "supported_actions": ["generateContent"],
            }
        )
        == SUPPORTED_CATEGORY
    )
    assert categorize_model("gemini-1.5-pro") == SUPPORTED_CATEGORY
    assert categorize_model({"name": "gemma-2-9b-it"}) == SUPPORTED_CATEGORY

    # Image Generation
    assert (
        categorize_model(
            {
                "name": "models/imagen-3.0-generate-002",
                "supported_actions": ["generateImages"],
            }
        )
        == "Image Generation"
    )
    assert categorize_model("imagen-3.0") == "Image Generation"
    assert (
        categorize_model(
            {
                "name": "models/nano-banana",
                "display_name": "Nano Banana",
                "description": "State-of-the-art image generation",
                "supported_actions": ["generateContent"],
            }
        )
        == "Image Generation"
    )
    assert categorize_model("nano-banana") == "Image Generation"

    # Audio Generation
    assert categorize_model({"name": "chirp-v2"}) == "Audio Generation"
    assert categorize_model({"name": "tts-1"}) == "Audio Generation"
    assert (
        categorize_model(
            {"name": "models/lyria-2", "description": "Music generation model"}
        )
        == "Audio Generation"
    )
    assert categorize_model("lyria-2") == "Audio Generation"
    assert (
        categorize_model(
            {
                "name": "models/gemini-transcribe-001",
                "description": "Audio transcription",
            }
        )
        == "Audio Generation"
    )
    assert categorize_model("gemini-transcribe") == "Audio Generation"
    assert (
        categorize_model(
            {
                "name": "models/some-model",
                "description": "Features high quality TTS outputs",
            }
        )
        == "Audio Generation"
    )
    assert (
        categorize_model(
            {
                "name": "models/some-music-model",
                "description": "Generates background music",
            }
        )
        == "Audio Generation"
    )
    assert (
        categorize_model(
            {
                "name": "models/audio-processor",
                "description": "A model for audio processing",
            }
        )
        == "Audio Generation"
    )
    assert (
        categorize_model(
            {
                "name": "models/whisper-variant",
                "description": "Can transcribe speech quickly",
            }
        )
        == "Audio Generation"
    )

    # Video Generation
    assert (
        categorize_model({"name": "models/veo-2.0-generate-001"}) == "Video Generation"
    )

    # Embeddings
    assert (
        categorize_model(
            {"name": "models/text-embedding-004", "supported_actions": ["embedContent"]}
        )
        == "Embeddings"
    )

    # Other
    assert categorize_model({"name": "models/aqa"}) == "Other / Specialized"
    assert (
        categorize_model(
            {
                "name": "models/gemini-robotics-001",
                "description": "Embodied robotics foundation model",
            }
        )
        == "Other / Specialized"
    )
    assert (
        categorize_model(
            {
                "name": "models/gemini-2.0-flash-realtime",
                "supported_actions": ["bidiGenerateContent"],
            }
        )
        == "Other / Specialized"
    )
    assert (
        categorize_model(
            {
                "name": "models/gemini-agent-exp",
                "supported_actions": ["generateContent"],
            }
        )
        == "Other / Specialized"
    )
    assert (
        categorize_model(
            {"name": "models/custom-model", "description": "An autonomous coding agent"}
        )
        == "Other / Specialized"
    )


def test_build_models_buffer():
    models = [
        {"name": "gemini-2.5-flash", "display_name": "Gemini 2.5 Flash"},
        {"name": "gemini-2.5-pro", "display_name": "Gemini 2.5 Pro"},
        {"name": "imagen-3.0", "display_name": "Imagen 3"},
        {"name": "text-embedding-004", "display_name": "Embedding 004"},
    ]

    buffer_lines, line_map, current_model_line = build_models_buffer(
        models, current_model="gemini-2.5-flash"
    )

    # Header check
    assert any("[Multimodal & Text Generation]" in line for line in buffer_lines)
    assert any("[Image Generation]" in line for line in buffer_lines)
    assert any("[Embeddings]" in line for line in buffer_lines)

    # Current model prefix check
    current_line = buffer_lines[current_model_line - 1]
    assert current_line == " * Gemini 2.5 Flash"

    # Check non-current model prefix
    pro_line = next(line for line in buffer_lines if "Gemini 2.5 Pro" in line)
    assert pro_line == "   Gemini 2.5 Pro"


def test_select_supported_model():
    models = [
        {"name": "gemini-2.5-flash", "display_name": "Gemini 2.5 Flash"},
        {"name": "imagen-3.0", "display_name": "Imagen 3"},
    ]
    buffer_lines, line_map, _ = build_models_buffer(models, current_model=None)
    models_module._VIMINI_MODELS = models
    models_module._LINE_TO_MODEL = line_map

    # Find line of gemini-2.5-flash
    target_line = next(
        ln
        for ln, item in line_map.items()
        if item and item["name"] == "gemini-2.5-flash"
    )

    import vim

    vim.current.window.cursor = (target_line, 3)
    mock_buffer = list(buffer_lines)

    with (
        patch.object(vim.current, "buffer", mock_buffer),
        patch("vimini.main.send_setup") as mock_setup,
        patch("vimini.util.display_message") as mock_display,
    ):
        select_model()
        assert util._MODEL == "gemini-2.5-flash"
        mock_setup.assert_called_once()
        mock_display.assert_called_once_with("Switched model to gemini-2.5-flash")


def test_select_unsupported_model_emits_error():
    models = [
        {"name": "gemini-2.5-flash", "display_name": "Gemini 2.5 Flash"},
        {"name": "imagen-3.0", "display_name": "Imagen 3"},
    ]
    util._MODEL = "gemini-2.5-flash"
    buffer_lines, line_map, _ = build_models_buffer(
        models, current_model="gemini-2.5-flash"
    )
    models_module._VIMINI_MODELS = models
    models_module._LINE_TO_MODEL = line_map

    # Find line of imagen-3.0
    target_line = next(
        ln for ln, item in line_map.items() if item and item["name"] == "imagen-3.0"
    )

    import vim

    vim.current.window.cursor = (target_line, 3)
    mock_buffer = list(buffer_lines)

    with (
        patch.object(vim.current, "buffer", mock_buffer),
        patch("vimini.main.send_setup") as mock_setup,
        patch("vimini.util.display_message") as mock_display,
    ):
        select_model()
        # Model should not have changed
        assert util._MODEL == "gemini-2.5-flash"
        mock_setup.assert_not_called()
        # Error should be displayed
        mock_display.assert_called_once_with(
            "Category 'Image Generation' is unsupported", error=True
        )
