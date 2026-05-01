# Architecture Proposal: Safe Agentic Workflows in Vimini

## Overview
This document outlines the architectural changes required to enable safe agentic workflows in the Vimini plugin. By upgrading the chat session to function as an autonomous agent, users can request multi-step actions (e.g., reading files, searching code, executing shell commands). The architecture prioritizes safety by requiring explicit user confirmation before executing any tool.

## 1. User Input
The user interacts with the agent using the existing `:ViminiChat` interface.
- **Natural Language Commands**: Users provide prompts like *"Find all TODOs in the codebase and write a summary to a new buffer"* or *"Read `src/main.py` and execute the unit tests."*
- **Continuous Session**: Because the chat operates in a continuous `session.send_message_stream` loop, the agent can iterate. The user only provides the initial intent, and the agent calls tools to satisfy the request.

## 2. Invoking the AI (Agent Configuration)
The chat session in `python3/vimini/chat.py` must be upgraded to support tools:
- **Define Tools**: Use `google.genai.types.Tool` and `FunctionDeclaration` to define capabilities like `execute_vim_command`, `read_file`, `write_file`, and `run_shell_command`.
- **System Instruction**: Set a `system_instruction` in the `GenerateContentConfig` instructing the AI to use these tools to fulfill user requests autonomously.
- **Session Initialization**: Pass the configuration to `client.chats.create()`.

## 3. Intercepting Tool Calls & Action Execution
When the AI decides to perform an action, it yields a `function_call` instead of plain text. The workflow to handle this safely is:

1. **Stream Interception**: In `chat.py`, the `target` callback's stream iterator is wrapped to inspect each chunk. If a `function_call` is detected, the wrapper pauses the AI's generation stream.
2. **Safety Prompt (User Confirmation)**: The system extracts the function name and arguments, and displays a Vim confirmation popup (e.g., `Execute: rm -rf target? [y/N]`).
3. **Execution**:
   - If approved, Python executes the requested action (e.g., using `subprocess.run` or `vim.command`).
   - If denied, an error or "access denied" status is recorded.
4. **Function Response**: The result of the action (stdout/stderr or file content) is packaged into a `function_response` Part and sent back to the AI session via `session.send_message(...)`.
5. **Resuming the Loop**: The AI receives the execution results and either calls another tool or concludes the workflow with a natural language summary.

## Summary of Code Changes
- **`vimini/chat.py`**: Added `agent_tools` and injected them into the session config. Provided a `stream_wrapper` in the `target` function to catch and format `function_call` elements.
- **Next Steps (Pending)**: Update the event queue in `vimini/util.py` to support bidirectional communication for the safety prompt, allowing the Vim UI to block and send user confirmation back to the background thread.
