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

Implemented and Documented elsewhere

---

## 4. ProxyAPI Implementation Details

Implemented and Documented elsewhere

---

## 5. Runtime Backend Switching & Vim UI

Implemented and Documented elsewhere

---

## 6. Agent Server & IPC Protocol Updates

Implemented and Documented elsewhere

---

## 7. Implementation Roadmap

1. **Phase 1: Config Management**: [COMPLETED] Implement configuration loader and automatic v1 default generator for `~/.var/vimini/config.json`.
2. **Phase 2: Abstraction & GenAI Adapter**: [COMPLETED] Refactor Google GenAI code into `GenAIBackend` matching `BaseClient` / `BaseChatSession` and adding `has_history()`.
3. **Phase 3: ProxyAPI Client**: [COMPLETED] Implement standard-library OpenAI client (`urllib.request`) supporting models listing, streaming completions, and tool calling.
4. **Phase 4: Agent Server & Session Integration**: [COMPLETED] Update `server.py`, `chat.py`, `code.py`, and `review.py` to use backend factory and honor `has_history()`.
5. **Phase 5: Vim Frontend & Commands**: [COMPLETED] Implement `:ViminiBackend` (with status echo and argument switching), single-group OpenAI model listing in `:ViminiListModels`, `:ViminiStatus`, and help documentation.
6. **Phase 6: Testing & Validation**: [COMPLETED] Add unit tests for config generation/parsing, backend switching, ProxyAPI mock requests, and single-group model listing.

## 8. Implementation strategy

As implementation progresses, phases are marked as complete, and information is
moved to the README file in concise form and only as necessary, information that
is no longer relevant because a phase or feature is already implemented is
simply removed and the section content is replaced with the standard text
"Implemented and Documented elsewhere"
