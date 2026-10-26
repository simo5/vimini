# ProxyAPI Integration Plan

This document outlines the architectural plan for adding support for OpenAI-compatible APIs (**ProxyAPI**) alongside the default Google Gemini (**GenAI**) backend in Vimini.

---

## 1. Overview & Goals

- **Dual Backend Architecture**: Support both the default Google Gemini backend (`genai`) and an OpenAI-compatible proxy or model provider backend (`ProxyAPI`).
- **De Facto Standard Compatibility**: Enable compatibility with standard OpenAI API endpoints (such as OpenAI, OpenRouter, LiteLLM, Ollama, vLLM, and LocalAI). Anthropic protocol support is left as a future extension.
- **Persistent Configuration**: Store endpoints, API key file paths, default models, and custom headers in `~/.var/vimini/config.json`.
- **Automatic Default Generation**: If `~/.var/vimini/config.json` does not exist, automatically generate a v1 configuration file based on current settings and behavior.
- **Dynamic Runtime Switching**: Allow switching between backends at runtime via `:ViminiBackend` without restarting Vim or the background agent server.
- **Zero Additional Dependencies**: Implement the ProxyAPI HTTP and streaming communication using Python's standard library (`urllib.request` / `http.client`, `json`, `ssl`), adhering to Vimini's guideline of avoiding third-party dependencies outside `google-genai`.

---

## 2. Configuration & Credential Management

Implemented and Documented elsewhere

---

## 3. Backend Abstraction Layer

A modular backend architecture isolates provider-specific communication behind a unified interface:

```
python3/vimini/common/
├── backends/
│   ├── __init__.py      # Backend registry and factory: get_backend(name, config)
│   ├── base.py          # Abstract interfaces: BaseClient, BaseChatSession, StreamChunk
│   ├── genai_backend.py # Google Gemini implementation (using google-genai)
│   └── proxy_backend.py # OpenAI-compatible implementation (using standard library HTTP/SSE)
```

### 3.1 Core Abstraction (`base.py`)

- **`BaseClient`**:
  - `list_models() -> List[Dict]`: Returns standardized model records (`name`, `display_name`, `description`, `supported_actions`).
  - `generate_content(prompt, model, system_instruction=None, config=None) -> str`: Non-streaming generation for commit messages, autocomplete, etc.
  - `generate_content_stream(prompt, model, system_instruction=None, config=None) -> Iterator[StreamChunk]`: Streaming generation for code generation.
  - `create_chat(model, system_instruction, tools, history=None) -> BaseChatSession`: Interactive multi-turn chat sessions.

- **`BaseChatSession`**:
  - `send_message_stream(prompt_or_tool_responses) -> Iterator[StreamChunk]`: Sends prompts or tool results and yields chunks.
  - `has_history() -> bool`: Returns whether the backend supports history retrieval. For `proxy_backend` (`openai`), this returns `False`.
  - `get_history() -> List[Dict]`: Retrieves serializable chat history if supported (`has_history() == True`). Callers will check `has_history()` before attempting history operations.

- **`StreamChunk`**:
  - `text`: Incremental generation text.
  - `thought`: Incremental reasoning/thinking content (supporting DeepSeek-R1 `<think>` delimiters and OpenAI `reasoning_content` deltas).
  - `tool_calls`: List of requested tool invocations (`name`, `args`, `call_id`).

---

## 4. ProxyAPI Implementation Details

### 4.1 Standard Library HTTP & SSE Client
- Build an SSE (Server-Sent Events) parser over `urllib.request` / `http.client`.
- Stream response deltas line by line (`data: { ... }`) to yield real-time text and thoughts to Vim.

### 4.2 Tool & Function Calling Translation
- **Definitions**: Translate Vimini tool declarations (`apply_patch`, `read_file`, `list_directory`, `list_git_commits`, `get_git_commit`, `git_grep`, `build_code`, `test_code`, `fix_format`) into OpenAI `tools` schema format:
  ```json
  {
    "type": "function",
    "function": {
      "name": "read_file",
      "description": "...",
      "parameters": { ... }
    }
  }
  ```
- **Responses**: Reconstruct tool calls from streaming `tool_calls` chunks, prompt user in Vim, and return results to the model using `{ "role": "tool", "tool_call_id": id, "content": result }`.

---

## 5. Runtime Backend Switching & Vim UI

### 5.1 Vimscript Commands & Options (`plugin/vimini.vim`)
- **Global Variable**: `g:vimini_backend` (the name of the active backend, e.g. `'Gemini'`, `'Custom 1'`).
- **Command**: `:ViminiBackend [backend_name]`
  - **Without arguments**: Echoes the active backend and lists other available backends in parentheses.
    - Example: `Gemini (available: Custom 1, Custom 2)`
  - **With argument**: Validates that the requested backend exists in `~/.var/vimini/config.json`. If valid:
    - Sets active backend in runtime and config.
    - Updates `g:vimini_backend`.
    - Updates `g:vimini_model` to the new backend's `default_model`.
    - Sends an updated `setup` message to the agent server.

### 5.2 Model List & Status Integration
- `:ViminiListModels`:
  - For `genai` backends, models are categorized by supported actions (Multimodal, Embeddings, Image, etc.).
  - For `openai` (ProxyAPI) backends, all models returned by `GET /v1/models` are listed under a single group: `[Available Models]`.
  - Displays the active backend name in the buffer header.
- `:ViminiStatus`: Displays active backend name, protocol type, endpoint URL, and current model.

---

## 6. Agent Server & IPC Protocol Updates

### 6.1 Setup Message Payload
Update `send_setup()` in `main.py` to transmit the active backend name:

```json
{
  "jsonrpc": "2.0",
  "id": "setup",
  "method": "setup",
  "params": {
    "backend": "Custom 1"
  }
}
```

Only the selected backend name is sent; the agent server reads `~/.var/vimini/config.json` to load the corresponding backend configuration options.

### 6.2 Agent Server Handlers
- Update `execute_function` in `server.py` to route `list_models`, `commit`, and `autocomplete` through the active backend client.
- Update `ChatSession` in `agent/chat.py`, `CodeSession` in `agent/code.py`, and `ReviewSession` in `agent/review.py` to obtain their client and session via `get_backend(agent_config)`.
- Update chat session history callers to check `has_history()` and bypass history handling if false.

---

## 7. Implementation Roadmap

1. **Phase 1: Config Management**: [COMPLETED] Implement configuration loader and automatic v1 default generator for `~/.var/vimini/config.json`.
2. **Phase 2: Abstraction & GenAI Adapter**: Refactor Google GenAI code into `GenAIBackend` matching `BaseClient` / `BaseChatSession` and adding `has_history()`.
3. **Phase 3: ProxyAPI Client**: Implement standard-library OpenAI client (`urllib.request`) supporting models listing, streaming completions, and tool calling.
4. **Phase 4: Agent Server & Session Integration**: Update `server.py`, `chat.py`, `code.py`, and `review.py` to use backend factory and honor `has_history()`.
5. **Phase 5: Vim Frontend & Commands**: Implement `:ViminiBackend` (with status echo and argument switching), single-group OpenAI model listing in `:ViminiListModels`, `:ViminiStatus`, and help documentation.
6. **Phase 6: Testing & Validation**: Add unit tests for config generation/parsing, backend switching, ProxyAPI mock requests, and single-group model listing.

## 8. Implementation strategy

As implementation progresses, phases are marked as complete, and information is
moved to the README file in concise form and only as necessary, information that
is no longer relevant because a phase or feature is already implemented is
simply removed and the section content is replaced with the standard text
"Implemented and Documented elsewhere"
