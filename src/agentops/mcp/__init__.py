"""MCP (Model Context Protocol) server for AgentOps.

Exposes the most-used AgentOps capabilities as MCP tools over stdio so that
MCP-aware coding agents (Claude Code, Copilot Chat, etc.) can drive AgentOps
without shelling out to the CLI.

The server is opt-in: it requires the optional ``mcp`` extra
(``pip install agentops-toolkit[mcp]``) and is started via
``agentops mcp serve``.
"""
