# Observe

This page explains how AgentOps turns agent observability into release evidence
and regression coverage. Foundry and Azure Monitor produce the runtime signal;
AgentOps reads that signal so readiness reflects what is actually happening in
production, not just what passed in CI.

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
    Generated eval and Doctor workflows install OpenTelemetry support.
    Eval runs emit `agentops.eval.*` spans and scheduled Doctor runs emit
    `agentops.agent.finding.*` spans, both of which the Cockpit can deep-link
    into Azure Monitor Logs.

## OpenTelemetry spans and semantic conventions

An OpenTelemetry trace is a tree of spans. Each span represents one unit of
work and records a name, start and end timestamps, status, attributes, and its
parent/child relationship to other spans. The OpenTelemetry generative AI
semantic conventions give those spans consistent names and `gen_ai.*`
attributes, so the same telemetry remains meaningful in Foundry and Azure
Monitor.

| Work | Span and operation | Key attributes |
|---|---|---|
| Agent invocation | `invoke_agent <agent-name>`; `gen_ai.operation.name=invoke_agent` | `gen_ai.agent.name`, `gen_ai.agent.id`, `gen_ai.conversation.id` |
| Model call | `chat <model>`; `gen_ai.operation.name=chat` | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` |
| Tool execution | `execute_tool <tool-name>`; `gen_ai.operation.name=execute_tool` | `gen_ai.tool.name` |

Domain attributes such as `helpdesk.ticket.queue` can add business context, but
they complement rather than replace the standard `gen_ai.*` attributes.

!!! warning "Keep sensitive content out of telemetry"
    Do not record secrets, tokens, or personal data in span attributes.
    `gen_ai.input.messages`, `gen_ai.output.messages`, and tool arguments or
    results can contain sensitive content, so omit, minimize, or redact them.

## Configure an agent to emit telemetry

Agents hosted in Foundry receive server-side tracing after you connect an
Application Insights resource to the project. If you own application code
around the agent call, add client-side instrumentation to capture that custom
logic as well.

Install the Azure Monitor OpenTelemetry distribution:

```bash
pip install azure-monitor-opentelemetry
```

Set `APPLICATIONINSIGHTS_CONNECTION_STRING` in the process environment; using
an application setting or secret reference is recommended in production. Then
configure Azure Monitor once during application startup:

```python
from azure.monitor.opentelemetry import configure_azure_monitor
from opentelemetry.sdk.resources import Resource

configure_azure_monitor(
    resource=Resource.create({"service.name": "helpdesk-agent"}),
)
```

Do not hardcode the connection string in source. After startup, run the agent
and verify a new trace in **Foundry > Agents > Traces** or **Application
Insights > Investigate > Agents (Preview)**. See the official
[Foundry tracing setup](https://learn.microsoft.com/azure/foundry/observability/how-to/trace-agent-setup)
for the complete connection, permissions, and verification flow.

## Instrument a custom Python agent

Once Azure Monitor is configured, application code can reuse the global tracer
provider without depending on an agent framework:

```python
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

tracer = trace.get_tracer("contoso.helpdesk.agent")


def lookup_ticket(ticket_id: str) -> str:
    return f"Ticket {ticket_id} is queued for review."


def run_agent(ticket_id: str, conversation_id: str) -> str:
    agent_attributes = {
        "gen_ai.operation.name": "invoke_agent",
        "gen_ai.agent.name": "helpdesk-agent",
        "gen_ai.agent.id": "helpdesk-agent-v1",
        "gen_ai.conversation.id": conversation_id,
    }
    with tracer.start_as_current_span(
        "invoke_agent helpdesk-agent",
        attributes=agent_attributes,
        record_exception=False,
        set_status_on_exception=False,
    ) as agent_span:
        try:
            with tracer.start_as_current_span(
                "execute_tool lookup_ticket",
                attributes={
                    "gen_ai.operation.name": "execute_tool",
                    "gen_ai.tool.name": "lookup_ticket",
                },
                record_exception=False,
                set_status_on_exception=False,
            ) as tool_span:
                try:
                    ticket = lookup_ticket(ticket_id)
                except Exception as exc:
                    tool_span.set_attribute("error.type", type(exc).__name__)
                    tool_span.set_status(Status(StatusCode.ERROR))
                    raise

            agent_span.set_status(Status(StatusCode.OK))
            return f"Helpdesk response: {ticket}"
        except Exception as exc:
            agent_span.set_attribute("error.type", type(exc).__name__)
            agent_span.set_status(Status(StatusCode.ERROR))
            raise
```

The span context managers preserve the parent/child relationship and finish
each span with its measured duration automatically. The example disables
automatic exception recording and records only `error.type`, avoiding exception
messages, stack traces, and tool arguments in telemetry.

For a more specific policy-span implementation, see the
[`acs_middleware.py` example](https://github.com/placerda/safe-agent-on-foundry/blob/main/src/helpdeskbot/acs_middleware.py#L280).
The official guide to
[trace structure and semantic conventions](https://learn.microsoft.com/azure/foundry/how-to/develop/langchain-traces#understand-trace-structure)
describes the same agent, model, and tool span hierarchy in detail.

## Traces as evaluation signal

A single trace shows what one request did. The value for release readiness comes
from reading many traces at once: latency percentiles, error rates, and the
evaluation results Foundry records as `gen_ai.evaluation.result` events.

The Doctor turns this into findings. It reads App Insights for p95 latency and
error rate, and it reports when telemetry is connected but silent, so a project
with no monitoring does not look healthy simply because nothing is being graded.

!!! note "Real telemetry surfaces production findings"
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
