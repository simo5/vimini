# Introduction

> Get started with the Agent Client Protocol.

The Agent Client Protocol (ACP) standardizes communication between code editors/IDEs and coding agents and is suitable for both local and remote scenarios.

## Why ACP?

AI coding agents and editors are tightly coupled but interoperability isn't the default. Each editor must build custom integrations for every agent they want to support, and agents must implement editor-specific APIs to reach users.
This creates several problems:

* Integration overhead: Every new agent-editor combination requires custom work
* Limited compatibility: Agents work with only a subset of available editors
* Developer lock-in: Choosing an agent often means accepting their available interfaces

ACP solves this by providing a standardized protocol for agent-editor communication, similar to how the [Language Server Protocol (LSP)](https://microsoft.github.io/language-server-protocol/) standardized language server integration.

Agents that implement ACP work with any compatible editor. Editors that support ACP gain access to the entire ecosystem of ACP-compatible agents.
This decoupling allows both sides to innovate independently while giving developers the freedom to choose the best tools for their workflow.

## Overview

ACP assumes that the user is primarily in their editor, and wants to reach out and use agents to assist them with specific tasks.

ACP is suitable for both local and remote scenarios:

* **Local agents** run as sub-processes of the code editor, communicating via JSON-RPC over stdio.
* **Remote agents** can be hosted in the cloud or on separate infrastructure, communicating over HTTP or WebSocket

<Info>
  Full support for remote agents is a work in progress. We are actively
  collaborating with agentic platforms to ensure the protocol addresses the
  specific requirements of cloud-hosted and remote deployment scenarios.
</Info>

The protocol re-uses the JSON representations used in MCP where possible, but includes custom types for useful agentic coding UX elements, like displaying diffs.

The default format for user-readable text is Markdown, which allows enough flexibility to represent rich formatting without requiring that the code editor is capable of rendering HTML.


# Architecture

> Overview of the Agent Client Protocol architecture.

The Agent Client Protocol defines a standard interface for communication between AI agents and client applications. The architecture is designed to be flexible, extensible, and platform-agnostic.

## Design Philosophy

The protocol architecture follows several key principles:

1. **MCP-friendly**: The protocol is built on JSON-RPC, and re-uses MCP types where possible so that integrators don't need to build yet-another representation for common data types.
2. **UX-first**: It is designed to solve the UX challenges of interacting with AI agents; ensuring there's enough flexibility to render clearly the agents intent, but is no more abstract than it needs to be.
3. **Trusted**: ACP works when you're using a code editor to talk to a model you trust. You still have controls over the agent's tool calls, but the code editor gives the agent access to local files and MCP servers.

## Setup

When the user tries to connect to an agent, the editor boots the agent sub-process on demand, and all communication happens over stdin/stdout.

Each connection can support several concurrent sessions, so you can have multiple trains of thought going on at once.

<img src="https://mintcdn.com/zed-685ed6d6/ZwvtxaoaZwBJrK5s/images/server-client.svg?fit=max&auto=format&n=ZwvtxaoaZwBJrK5s&q=85&s=336b604262b9b61ece9fc402774185bf" alt="Server Client setup" width="579" height="455" data-path="images/server-client.svg" />

ACP makes heavy use of JSON-RPC notifications to allow the agent to stream updates to the UI in real-time. It also uses JSON-RPC's bidirectional requests to allow the agent to make requests of the code editor: for example to request permissions for a tool call.

## MCP

Commonly the code editor will have user-configured MCP servers. When forwarding the prompt from the user, it passes configuration for these to the agent. This allows the agent to connect directly to the MCP server.

<img src="https://mintcdn.com/zed-685ed6d6/ZwvtxaoaZwBJrK5s/images/mcp.svg?fit=max&auto=format&n=ZwvtxaoaZwBJrK5s&q=85&s=87650126080bf3bb4d983f447889ab86" alt="MCP Server connection" width="689" height="440" data-path="images/mcp.svg" />

The code editor may itself also wish to export MCP based tools. Instead of trying to run MCP and ACP on the same socket, the code editor can provide its own MCP server as configuration. As agents may only support MCP over stdio, the code editor can provide a small proxy that tunnels requests back to itself:

<img src="https://mintcdn.com/zed-685ed6d6/ZwvtxaoaZwBJrK5s/images/mcp-proxy.svg?fit=max&auto=format&n=ZwvtxaoaZwBJrK5s&q=85&s=dd128411c4be5945e131596190b41cb8" alt="MCP connection to self" width="632" height="440" data-path="images/mcp-proxy.svg" />


