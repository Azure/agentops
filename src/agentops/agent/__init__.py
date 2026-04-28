"""AgentOps Watchdog Agent.

The watchdog agent reads three signal sources (AgentOps eval history,
Azure Monitor / App Insights traces, Foundry control plane), runs a
set of checks over the gathered data, and produces a Markdown findings
report. It is exposed both as a CLI (``agentops agent analyze``) and as
a Copilot Extension HTTP server (``agentops agent serve``).
"""

from agentops.agent.findings import Finding, Severity

__all__ = ["Finding", "Severity"]
