# Proxy API TODO

Future work for Vimini's proxy/API backend support.

## Anthropic API support

- Add a native Anthropic backend in addition to the current OpenAI-compatible proxy backend.
- Map Vimini's generic chat, streaming, tools, and tool-response abstractions to Anthropic's Messages API.
- Handle Anthropic-specific streaming events and content block formats.
- Add global config support for `api: "anthropic"`, including model defaults and API key file loading.
- Add tests for model listing behavior, streaming responses, tool calls, and error handling.

## OpenAI Files API for context files

- Add support for OpenAI-compatible Files API uploads so proxy/openai backends can handle Vimini context files similarly to the Gemini backend.
- Track uploaded file IDs and metadata per backend/session where needed.
- Decide how context files should be attached to requests for supported providers and models.
- Add cleanup/list/delete support for uploaded files through `:ViminiFiles` where the provider supports it.
- Add graceful fallback behavior for providers that expose OpenAI-compatible chat APIs but do not support files.
