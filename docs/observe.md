# Observe

This page explains how AgentOps turns agent observability into release evidence
and regression coverage. Foundry and Azure Monitor produce the runtime signal;
AgentOps reads that signal so readiness reflects what is actually happening in
production, not just what passed in CI.

Observability is conceptual here. For the hands-on portal and KQL walkthrough,
see step 18 of the [Foundry Prompt Agent tutorial](tutorial-prompt-agent.md).

## Where the signal comes from

Foundry gives you the runtime view of an agent: traces, conversations, spans,
latency, and model calls per run. Behind that view, Foundry emits telemetry to
**Azure Monitor / Application Insights**, where requests, errors, and evaluation
events are stored and queryable.

AgentOps does not replace either surface. It reads them so the same runtime
truth feeds the readiness story alongside eval results and Doctor findings.

## What AgentOps reads

AgentOps connects to Application Insights through
`APPLICATIONINSIGHTS_CONNECTION_STRING`. When a Foundry project endpoint is set,
AgentOps first tries to auto-discover the project's App Insights resource and
falls back to that connection string when discovery is not available.

!!! info "Telemetry from CI runs"
    Generated eval and Doctor workflows install AgentOps telemetry support.
    Eval runs emit `agentops.eval.*` spans and scheduled Doctor runs emit
    `agentops.agent.finding.*` spans, both of which the Cockpit can deep-link
    into Azure Monitor Logs.

## Traces as evaluation signal

A single trace shows what one request did. The value for release readiness comes
from reading many traces at once: latency percentiles, error rates, and the
evaluation results Foundry records as `gen_ai.evaluation.result` events.

The Doctor turns this into findings. It reads App Insights for p95 latency and
error rate, and it reports when telemetry is connected but silent, so a project
with no monitoring does not look healthy simply because nothing is being graded.

!!! note "Real telemetry produces honest findings"
    Because the Doctor reads live runtime data, it can surface latency or error
    findings from your own production traffic, separate from the eval gate. That
    is intended: a real release should investigate latency and errors before
    promoting, even when the candidate's eval scores pass.

## Agent identity on traces

Traces tell you what an agent did. They do not, by default, tell you *which*
agent did it in a way an auditor can reconcile with your tenant. Microsoft
Entra Agent ID closes that gap: the agent gets a first-class identity, and the
same identifier travels from registration through traces into release evidence.

The handshake has three steps, and each one is a different tool, so it is worth
being explicit about who writes what.

**1. Register the identity.** `agentops agent register` creates (or adopts) an
agent identity blueprint in Microsoft Entra and records the resolved
application id locally:

```bash
agentops agent register --sponsor owner@contoso.com
```

The sponsor is required. An agent identity with no accountable owner cannot be
governed, so there is no default. The command is idempotent: if a blueprint
with the same display name already exists, AgentOps reuses it instead of
creating a duplicate. Run it with `--dry-run` first to see the resolved display
name and sponsor without calling Microsoft Graph.

The resolved id is written to `.agentops/identity/agent-identity.json`. Declare
the inputs in `agentops.yaml` so they are source-controlled:

```yaml
identity:
  display_name: support-agent
  sponsor: owner@contoso.com
  verify: true
```

`verify: true` tells the Doctor to confirm the blueprint against Microsoft
Graph. It is off by default because that lookup needs tenant admin consent
(`AgentIdentityBlueprint.Read.All`), which most workspaces will not have on day
one. With it off, the Doctor still reports whether an identity is registered at
all, using only local state.

**2. Stamp it on traces.** Once an identity is resolved, AgentOps adds it to
the OpenTelemetry resource as `gen_ai.agent.id`, so every span AgentOps emits
carries the Entra Agent ID. In CI, where the local record is not checked in,
set `AGENTOPS_ENTRA_AGENT_ID` instead and the attribute resolves from the
environment.

The attribute is **omitted** when no identity is registered, never emitted as an
empty string. That distinction matters when you query: filtering on presence
tells you which traffic is attributable and which is not.

```kusto
dependencies
| where isnotempty(customDimensions["gen_ai.agent.id"])
| summarize runs = count() by tostring(customDimensions["gen_ai.agent.id"])
```

**3. Publish it as evidence.** The release evidence pack reads the same record
and adds an `agent_identity` section reporting the id and where it came from
(the local record or the environment variable). When no identity is registered,
the pack raises a warning rather than a blocker, because identity registration
is a governance improvement rather than a correctness gate.

!!! note "AgentOps does not ingest into Agent 365"
    There is no public ingestion API for Agent 365 telemetry today. AgentOps
    stamps the identifier and publishes it as evidence so the correlation is
    possible from the Azure Monitor side. It does not push traces into Agent
    365.

To browse this signal interactively and deep-link into Foundry and Azure
Monitor, run `agentops cockpit`. That local command center is covered on the
[Operate](operate.md#cockpit) page.

## Run from your coding agent

Install the AgentOps skills so your coding agent can read telemetry and
investigate production health.

```bash
agentops skills install --platform copilot
```

The skills that map to observability are:

| Skill | What it helps with |
|---|---|
| `agentops-agent` | Watchdog analysis of production health and latency spikes. |

## Next

Act on the signal over time on the [Operate](operate.md) page, feed passing
evidence back into the gate on the [Ship](ship.md) page, or harden the dataset
on the [Evaluation](evaluation.md) page.
