# Project Purpose: MCP Agent Hub

## The Problem
Local AI agents (such as Claude Code and Antigravity CLI) are powerful but inherently isolated. They operate as client-side CLI applications. When a user wants two independent agents to collaborate on a local machine, they face several hurdles:
1. CLI tools lack inbound network ports, meaning they cannot receive webhook calls or act as servers.
2. Ad-hoc workarounds (like file-system mailboxes) are fragile, prone to race conditions, and require extensive, repetitive prompting.
3. The lack of visibility into inter-agent communication makes debugging and supervision nearly impossible.

While standard enterprise protocols (like A2A) exist for multi-agent coordination, they are designed for web servers and are too heavyweight for simple local CLI orchestration. Similarly, while there are UI dashboards for Model Context Protocol (MCP), they focus on managing *tools* rather than coordinating *messages between agents*.

## The Solution
We will build the **MCP Agent Hub**: a lightweight, local message broker with a built-in web dashboard.

By leveraging the Model Context Protocol (MCP)—which both Claude Code and Antigravity support natively—we can create a centralized "Post Office." The Hub will act as an MCP Server that exposes standardized tools for agents to register themselves, discover other agents, send messages, and check their inbox. 

Alongside the MCP server, the Hub will run a FastAPI web dashboard. This allows human developers to observe the agent-to-agent traffic, view the registry of connected agents, and intervene if necessary.

## Goals
1. **Zero-Configuration Communication:** Agents send and receive messages reliably without hallucinating file names or juggling lockfiles. A recipient learns it has work by parking on a single blocking "check inbox" call, rather than spin-polling or waiting for a human to nudge it.
2. **Observability:** Provide a clean Web UI to view the live message queue and the roster of connected agents.
3. **Resilience:** Persist messages in SQLite and pair them with an explicit ack + visibility-timeout model, so that a task survives an agent crash or restart and is redelivered rather than silently lost.
4. **Standardization:** Establish a reusable local infrastructure that avoids "ad-hoc" prompting for every new project.
