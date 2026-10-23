# Contributing to Vimini

Thank you for contributing to Vimini! This document outlines guidelines and instructions for contributing to the project.

## Development Setup

- Python 3.11+ (Python 3.14 recommended)
- Vim or Neovim with Python 3 support
- `pytest` for running tests
- `ruff` for code formatting

You can install the development dependencies using `pip`:
```bash
pip install ruff pytest google-genai
```

## Code Formatting

All Python code in this project must be formatted using [Ruff](https://astral.sh/ruff).

To ensure predictable and consistent code outcomes, our Continuous Integration (CI) runs a format check on every push and pull request.

- **Check formatting locally:**
  ```bash
  ruff format --check .
  ```

- **Automatically format code:**
  ```bash
  ruff format .
  ```

If you are using Vimini's agent tools, you can also use the `fix_format` tool to automatically format the code.

## Continuous Integration (CI)

A GitHub Actions CI workflow runs automatically on pull requests and pushes to `main`/`master`. It checks that all Python files adhere to `ruff format --check .`.

Please ensure that `ruff format --check .` passes locally before opening a pull request or submitting code.

## Testing

Run the test suite using `pytest`:
```bash
pytest
```

To run a specific test file or test case:
```bash
pytest tests/test_command_config.py
pytest -k <test_name>
```

Please make sure all existing tests pass and add new tests when introducing changes or bug fixes.
