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

## Multi-project Observe

Observe can aggregate multiple Foundry resources and projects without hiding
where a value came from. The local workspace project is the default. An
operator may explicitly select project ARM resource IDs or widen the boundary
to one Foundry account, resource group, or subscription. The hosted application
stores that choice as one versioned `AGENTOPS_OBSERVE_SCOPE` setting; it does
not infer "all projects in the deployment resource group."

Discovery resolves each project's credential-free connection metadata to
Application Insights and its backing Log Analytics workspace. Shared workspaces
are queried once while retaining every Foundry/project origin. Observe limits a
request to ten telemetry sources, applies filters before aggregation, and keeps
successful source results when another source is denied, throttled, or times
out.

The standard six views share the same explicit filter state:

- **Overview** shows bounded aggregate activity, latency, errors, and observed
  token usage.
- **Agents** attributes observed activity to standard `gen_ai.*` identifiers.
  Every row includes its `source_id`, so the same agent observed through two
  telemetry sources remains distinguishable.
- **Models and usage** shows model/provider attribution and observed tokens,
  not billing or quota.
- **Tools** lists each observed tool by telemetry source, agent, and tool name,
  with invocation and failure counts, last-seen time, and p95 latency. Missing
  latency is shown as not measured, never as zero; tool token fields are
  intentionally not reported.
- **Runs** lists correlated agent executions by telemetry source and agent,
  with the correlation key and kind (`conversation` or `trace`), range-scoped
  start and duration, status, turns, failed turns, tool invocations, tool
  failures, and observed input/output token totals. Missing token totals are
  shown as not available, never as zero.
- **Telemetry coverage** explains readable, empty, denied, unconfigured,
  unattributed, protected, timed-out, and partial sources.

When `AGENTOPS_COST_MODEL` contains a valid version 1 model, a seventh
**Cost** view appears. Cost uses its configured period boundaries and component
selectors instead of the shared Observe time, source, project, model, tool, and
run filters. This prevents a display filter from silently changing an allocation
denominator.

The default range is 24 hours. Draft filters do not query until **Apply** is
selected. Applied filters are bookmarkable in the URL, refresh every five
minutes, and may be refreshed manually. Raw trace content is never part of that
URL or browser persistence. Alongside the existing agent, model, and time-range
filters, **Tools** accepts `tool_name` and **Runs** accepts `run_key`. Both only
narrow results; blank values are rejected, and values are escaped before they
reach telemetry queries.

## Allocate declared billed totals

The Cost view is an operational allocation of totals supplied by an operator.
AgentOps does not call Azure Cost Management, discover invoices, apply rate
cards, or infer credits. Removing `AGENTOPS_COST_MODEL` hides Cost and leaves
every other Observe view unchanged.

Configure the model as bounded JSON before starting Cockpit:

```powershell
$env:AGENTOPS_COST_MODEL = @'
{
  "version": 1,
  "periods": [{
    "id": "2026-08",
    "starts_at": "2026-08-01T00:00:00Z",
    "ends_at": "2026-09-01T00:00:00Z",
    "components": [{
      "id": "gpt-ptu-prod",
      "type": "provisioned_throughput",
      "billing_boundary": {
        "kind": "resource",
        "value": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/ai-prod/providers/Microsoft.CognitiveServices/accounts/foundry-prod"
      },
      "billed_source": "August provisioned-throughput commitment",
      "billed_total": "12000.00",
      "currency": "USD",
      "currency_minor_units": 2,
      "allocation_model": "commitment",
      "allocation_key": "weighted_tokens",
      "fallback_key": "total_tokens",
      "token_weights": {
        "input_tokens": "1",
        "output_tokens": "4",
        "cache_read_tokens": "0.25"
      },
      "usage_match": {
        "deployments": ["gpt-prod"]
      }
    }]
  }]
}
'@

agentops cockpit
```

The value is read once at process startup, is limited to 32 KiB, and rejects
unknown or secret-shaped fields. Restart Cockpit after changing or removing it.
Malformed configuration disables only Cost and returns a non-sensitive
validation error for direct Cost requests.

### Component compatibility and evidence

Each component declares one total, currency, precision, billing boundary, and
compatible usage key:

| Component | Preferred allocation evidence | Allowed fallback |
|---|---|---|
| Provisioned throughput | Weighted or total tokens | Total tokens when weighted is preferred |
| Standard model | Weighted or total tokens | Total tokens when weighted is preferred |
| Search, grounding, content safety, storage | Tool invocations | None |
| Hosted or customer compute | Active session seconds | None |
| Pay-as-you-go or prepaid credits | Directly reported credits or explicit configured credit events | Explicit configured credit events when credits are preferred |

Weighted tokens use only configured weights and directly observed token classes.
A missing class remains missing; it is never derived from another total. Direct
credits take precedence over credit-event fallback, and credit events count only
explicitly configured operation names. No event-to-credit rate is inferred.
`usage_match` narrows eligible observations by declared source, project, agent,
deployment, model, tool, or runtime values.

Every amount retains its billed source and boundary, selected allocation method,
observed numerator and denominator, observation window, source counts,
calculation time, freshness, and confidence. Confidence is:

- **high** when required telemetry is complete;
- **medium** when an explicit fallback is used;
- **low** when telemetry is partial;
- **unavailable** when no usable denominator exists.

### Exact reconciliation and truthful gaps

AgentOps allocates with decimal arithmetic in integer currency minor units. A
deterministic largest-remainder step uses the normalized consumer key to break
ties. For every component and currency:

```text
attributed + unattributed + unallocated = declared billed total
```

Agent, tool, and run breakdowns are alternative reconciliations of the same
billed pools; never add them together. Different currencies and minor-unit
precisions remain separate. Applying `cost_agent_key` happens after allocation:
hidden rows move to `omitted_allocated_amount` without changing denominators or
the original component allocation.

The UI and API keep these states distinct:

- **Not configured**: no declared total exists; this is not zero spend.
- **Observed zero**: telemetry explicitly reported zero.
- **Unattributed**: usage exists but lacks the selected agent, tool, or run key.
- **Unallocated**: a declared total has no usable observed denominator.
- **Omitted**: bounded or filtered allocated rows are excluded from display but
  remain in reconciliation summaries.
- **Partial**: one or more telemetry sources failed or reported incomplete
  dimensions; successful bounded results remain visible.

The hosted deployment may propagate the model as the allowlisted, non-secret
`AGENTOPS_COST_MODEL` App Service setting. It adds no Azure resource, identity,
role assignment, billing permission, or write permission. Existing aggregate
queries continue to use only `Reader` and `Log Analytics Reader`.

> Operational cost allocation from declared billed totals and observed usage;
> not an invoice or billing-accurate charge.

### Runtime attribution and source identity

`source_kind` now identifies the runtime more precisely. The accepted values
are `foundry_hosted`, `foundry_prompt`, `external_registered`,
`external_unregistered`, `copilot_studio`, and `unknown`. `unknown` means the
available telemetry cannot classify the runtime; it is an expected attribution
outcome, not an error.

This replaces the former `foundry`, `external`, and `unknown` contract. The
mapping is not one-to-one:

| Old value | New value(s) | Notes |
|---|---|---|
| `foundry` | `foundry_hosted` or `foundry_prompt` | Split by runtime. |
| `external` | `external_registered` or `external_unregistered` | Split by project registration. |
| — | `copilot_studio` | New classification with no predecessor. |
| `unknown` | `unknown` | Unchanged fallback when telemetry is insufficient. |

The old and new values are not emitted together. Consumers must update to the
refined values. `source_id` is also present on Agents, Tools, and Runs rows;
it identifies the telemetry source that produced the row and prevents rows from
different sources from being treated as one observation.

### Runs are scoped to the selected window

Runs describe only activity observed in the selected time range. If a run began
before the range, its reported start, duration, turns, failed turns, and status
cover only the in-window activity and it is not marked as truncated at the
leading edge. A failure before the range is therefore not visible and an
otherwise settled row can report `succeeded`. The `in_progress` status instead
covers the trailing edge: activity near the range end may still be settling.

### Truthful source and dimension states

Observe does not turn missing evidence into numeric zero. Every response carries
source attribution, refresh time, query duration, source counts, partial
failures, and dimension coverage. "Last seen" means observed activity in the
selected range, not deployment or lifecycle status.

| State | Meaning |
|---|---|
| Available | The source/dimension was readable and reported values. |
| No data | The source was readable and had no matching rows in the selected range. |
| Not configured | No linked telemetry source was found. |
| Inaccessible | The active identity could not read the source. |
| Not reported | Rows existed but the requested semantic dimension was absent. |
| Partial | Some bounded sources succeeded and others did not. |
| Error / timed out | The source failed or exceeded its query budget. |
| Protected or unavailable | Protected content could not be proven readable, including ambiguous zero-row results. |

### Aggregate access versus trace content

The shared UAMI reads discovery and aggregate telemetry with `Reader` and
`Log Analytics Reader`. It never receives `Privileged Monitoring Data Reader`.
Raw `AppGenAIContent` loads only after an explicit trace-detail action and uses
the signed-in user's delegated Azure Monitor permission through OBO, correlated
by source scope, `TraceId`, and `SpanId`. There is no legacy-field fallback.

Trace-content responses use `Cache-Control: no-store`; raw values never enter
shared caches, URLs, browser storage, telemetry, diagnostics, or deployment
artifacts. See [Operate](operate.md#shared-hosted-cockpit-and-protected-content)
and [Deploy the hosted Cockpit](deploy-hosted-cockpit.md).

## Enable user and department attribution

Attribution is opt-in. Set `AGENTOPS_ATTRIBUTION_CONFIG` on the Cockpit
deployment and restart it. An enabled version 1 configuration requires a stable,
random deployment namespace, a generation of at least `1`, and up to 100
department definitions. Each definition maps deployment-scoped pseudonymous
user keys or group object IDs to one department. The setting is non-secret but
privacy-sensitive; keep its value out of source control, logs, tickets, and
release artifacts. Deployment preview shows only its enabled state, generation,
fingerprint, department-definition count, user-key entry count, and group-ID
entry count.

Bootstrap mappings from the protected **Users** view:

1. Sign in as an operator who can read the selected telemetry scope directly.
2. Open **Users** using the delegated view and copy only the generated
   `usr1.g<generation>.<sha256>` key into the appropriate department definition.
3. Alternatively, configure a group object ID already present in the signed-in
   principal's validated claims. Group mapping applies only to that exact
   principal; AgentOps does not query Microsoft Graph or enrich identities.
4. Preview the deployment, review the widened delegated-data warning, confirm,
   and restart Cockpit.

Only non-empty `UserAuthenticatedId` and OpenTelemetry `enduser.id` values are
eligible identity sources. `UserId`, `enduser.pseudo.id`, session, device,
browser, network, prompt, and behavioral values are never identity fallbacks.
Conflicting eligible aliases are ambiguous and remain unattributed.

### Access, coverage, and privacy boundary

Department aggregates that cannot identify a singleton may use the deployment
identity and shared aggregate cache. Every individual view, user filter, and
singleton-department result uses a fresh signed-in-user OBO credential, bypasses
shared caches, and returns `Cache-Control: private, no-store`. Missing delegated
access or direct Log Analytics RBAC fails closed without retrying as the
deployment identity. Enabling attribution adds no role, Graph permission,
directory read, write capability, or secret; the deployment identity retains
only `Reader` and `Log Analytics Reader`, while delegated requests reuse Azure
Monitor `Data.Read`.

Coverage is reported per source and measure. It distinguishes available,
partial, not reported, inaccessible/protected, ambiguous, and error states.
Unmapped and ambiguous consumption remains in unattributed totals, and a failed
source does not erase successful aggregate evidence from another source. Cost
attribution appears only when an existing declared cost allocation is available;
it does not change billed totals, allocation denominators, or reconciliation.
If the signed-in token reports group-claim overage while group mappings are
configured, attribution performs no Microsoft Graph or directory lookup.
Coverage is marked **partial** with fixed guidance to use explicit user mappings
or sign in with group claims within the supported token limit.

Pseudonymous keys and opaque filters are deterministic, linkable personal data,
not anonymous data. A namespace limits cross-deployment correlation but is not a
secret and does not prevent guessing low-entropy identities. Raw identities are
shown only in the current delegated Users response. Mapping values, raw
identities, user rows, group IDs, and filter tokens are excluded from shared
caches, application logs, Doctor output, deployment journals, and release
evidence. Aggregate readiness status, coverage states, generation, configuration
fingerprint, and non-identifying counts remain available.

### Rotate or disable attribution

Rotation is explicit, never time-based: generate a fresh random namespace,
increment `generation`, rebuild all user mappings from the protected Users view,
preview and confirm the deployment, then restart Cockpit. Existing keys and
bookmarked filters fail closed after rotation; there is no grace period or
previous-generation lookup.

To disable attribution, remove `AGENTOPS_ATTRIBUTION_CONFIG` or deploy a valid
configuration with `enabled: false`, then restart Cockpit. Attribution routes,
controls, filters, and coverage disappear while the existing Observe views and
aggregate access remain unchanged. Correct an invalid setting or remove it;
invalid attribution configuration does not take unrelated Cockpit diagnostics
offline.

### Attribution acceptance protocols

Use synthetic identities and a fixed dataset for both protocols. Do not export
the view.

For the SC-005 operator check, recruit at least ten representative operators
who have not seen the answer. Give every participant the same unfiltered
department Usage view and these instructions only: identify the
highest-consuming department, apply its department filter, copy the resulting
URL, open that URL in a new private window while signed in, and confirm the same
department and time range. Start one stopwatch when the unfiltered view is
shown and stop it after the restored view is confirmed. Record participant ID,
start/end timestamps, selected department correctness, restored-filter
correctness, elapsed seconds, and pass/fail in the acceptance record. A
participant passes only when both answers are correct and elapsed time is at
most 120 seconds. SC-005 passes when at least 90% pass; retain the aggregate
record, not identities or copied URLs.

For SC-006, the standard offline scope is one project, representative coverage
for three telemetry sources, 200 synthetic users, and the maximum 100
departments. Run 20 Usage and 20 Cost display samples after one warm-up and
record each elapsed duration from receipt of the bounded aggregate response
through completed HTML rendering. Compute p95 by nearest rank (the 19th sorted
sample of 20) separately for Usage and Cost. Both p95 values must be at most
five seconds. The repository acceptance test uses deterministic synthetic
responses and requires no Azure access:

```powershell
python -m pytest tests/unit/test_attribution_performance_acceptance.py -q
```

This controlled check detects application-side display regressions. Before a
release, repeat the same 20-sample protocol in the target connected environment
to include discovery, Azure Monitor query, network, and browser costs.

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

<a id="agent-identity-on-traces"></a>

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
