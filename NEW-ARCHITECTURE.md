# Vimini Next-Generation Architecture (ACP Aligned)

This document outlines the proposed transition of Vimini from a monolithic embedded Python plugin to a 2-component architecture based on the **Agent Client Protocol (ACP)**. This split will improve performance, stability, and isolation by moving the heavy AI logic and state management out of the Vim process, while standardizing how Vimini interacts with coding agents.

## 1. Architecture Overview

The new architecture consists of two main components communicating via ACP:

1. **Vimini Client (Vim Plugin)**
   - A thin Vimscript/Python layer living inside the Vim process.
   - Acts as an **ACP Client**, responsible strictly for UI interactions (buffers, popups, key mappings, diff rendering) and Vim state extraction.
   - Boots the agent sub-process on demand and communicates via JSON-RPC over `stdio`.

2. **Vimini Server (ACP Agent / External Daemon Process)**
   - A standalone Python process hosting the Google GenAI client, background threads, and state management.
   - Runs as a local agent sub-process of the code editor.
   - Handles all API rate limiting, long-running LLM requests, and context building.

## 2. Component Breakdown

### 2.1 The Thin Client Layer (Editor)
- **No blocking AI calls:** The embedded `py3` layer will no longer import `google.genai` or manage threads.
- **Sub-process Management:** When the user initiates a session, the editor boots the agent sub-process. Multiple concurrent sessions can be supported over the connection.
- **Asynchronous IPC:** Uses Vim's `channel` and `job` features (or Python's `asyncio`/`subprocess` within `py3`) to send JSON-RPC messages via `stdio`.
- **MCP Proxying:** The client can expose Vim-specific tools (like reading buffers or quickfix lists) by providing a small proxy that tunnels MCP requests back to itself.

### 2.2 The External Daemon Process (Agent)
- **ACP-Compliant Agent:** Runs entirely independently of the Vim main loop, communicating exclusively over `stdin/stdout` using the ACP standard.
- **MCP Integration:** Receives configuration for user-configured MCP servers from the client. The agent connects directly to these MCP servers to extend its capabilities.
- **Bidirectional Communication:** Can push real-time UX updates (like streaming chunks or UI elements) and make bidirectional requests to the client (e.g., requesting user permissions for tool calls).

## 3. Communication Protocol (Agent Client Protocol)
We will adopt the **Agent Client Protocol (ACP)**, replacing the previously proposed custom socket protocol.

- **JSON-RPC over Stdio:** Reliable, standardized communication.
- **MCP Type Re-use:** ACP re-uses Model Context Protocol (MCP) JSON representations where possible, minimizing the need to build custom data types.
- **UX-First:** Includes custom types for agentic coding UX elements (e.g., displaying diffs in markdown format without requiring HTML rendering capabilities in Vim).

**Client -> Server (User Prompt / Request)**
```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "agent/chat",
  "params": {
    "messages": [{"role": "user", "content": "Refactor this function"}],
    "mcp_servers": { ... }
  }
}
```

**Server -> Client (Streaming UX / Tool Notification)**
```json
{
  "jsonrpc": "2.0",
  "method": "window/logMessage",
  "params": {
    "type": 4,
    "message": "Applying diff to src/main.py..."
  }
}
```

## 4. MCP Integration Strategy

By adopting ACP, Vimini will natively support the Model Context Protocol (MCP):
1. **External MCP Servers:** The user configures external MCP servers (e.g., GitHub, local databases). The Vimini client passes this configuration to the Vimini agent, which connects to them directly.
2. **Vim-Native MCP Server:** The Vimini client will export its own MCP-based tools (like `read_vim_buffer` or `apply_patch`). The client acts as a small proxy, passing its self-hosted MCP configuration to the agent, allowing the agent to request actions from Vim seamlessly.

## 5. Migration Plan

1. **Phase 1: Abstract API Calls**
   - Refactor `vimini/util.py` and `vimini/main.py` to route all AI requests through a unified messaging interface.
2. **Phase 2: Build the ACP Agent Daemon**
   - Create an `agent.py` entry point that implements a JSON-RPC server over `stdio` following the ACP specification, loading the existing Vimini Python logic.
3. **Phase 3: Implement ACP Client in Vimini**
   - Replace the embedded Python threading with a sub-process spawner that sends payloads and polls for responses via `stdio`.
4. **Phase 4: MCP Tooling Integration**
   - Build the internal MCP proxy in the Vimini client to expose editor context to the agent, deprecating bespoke context-gathering functions.

## 6. Benefits of ACP Adoption
- **Ecosystem Interoperability:** By implementing ACP, the Vimini agent could potentially be used by other ACP-compatible editors, and the Vimini client could connect to other ACP-compatible agents.
- **Zero Blocking:** Completely eliminates Vim UI freezes during complex context gathering or API initialization.
- **Standardized Tooling:** MCP compatibility immediately grants Vimini access to a vast ecosystem of context providers without writing custom extensions.
- **Security & Trust:** The editor retains control, booting the agent in a trusted environment and explicitly granting it access to local files and specific MCP servers.
