# AGENTS.md

## Project Overview
Vimini is a Vim / Neovim plugin that integrates Google Gemini for AI-assisted chat, code generation, reviews, commits, and autocomplete.

## Repository Layout
- `plugin/vimini.vim`: Vimscript entry point and command definitions.
- `python3/vimini/`: Core Python implementation.
  - `agent/`: Background agent server (`server.py`), interactive chat sessions (`chat.py`), and IPC communication (`comms.py`).
  - `common/`: Gemini API client management (`genai.py`) and project utilities (`util.py`).
  - `chat.py`, `code.py`, `review.py`, `commit.py`, `config.py`, `context.py`, `ripgrep.py`, `models.py`: Vim buffer, UI, and command integrations.
- `tests/`: Test suite using `pytest` and `unittest.mock`. The `vim` module is mocked in `tests/conftest.py`.

## Testing & Verification
- Run test suite: `pytest`
- Run specific tests: `pytest -k <test_name>` or `pytest tests/<file_name>.py`

## Key Guidelines
- **Python Compatibility**: Targets Python 3.11+ (compatible with Python 3.14). Avoid introducing new third-party dependencies outside `google-genai`.
- **Mocking `vim`**: In tests, `vim` is mocked via `sys.modules["vim"]`. Use `patch.object(vim, "<attribute>", <mock_value>)` when patching `vim` attributes (e.g. `buffers`, `windows`).
- **Agent IPC**: Interaction between Vim and the agent server uses JSON IPC over local sockets.
