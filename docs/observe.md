# Observe

This page explains how AgentOps uses agent observability. Foundry and Azure
Monitor produce the runtime signal; AgentOps reads that signal so release
readiness reflects what is actually happening in production, not just what
passed in CI.

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

## Trace-to-regression promotion

The strongest use of observability is turning real production behavior into new
evaluation coverage. Reviewed production traces become new dataset rows, so the
cases your agent actually sees keep getting evaluated on every future run.

In Foundry, this is the trace-to-dataset flow: sample recent traces, let
intelligent sampling deduplicate and select a representative set, and create an
evaluation dataset from them. AgentOps then promotes that into reviewable
regression rows with `agentops eval promote-traces`.

!!! warning "Promotion is review-first"
    Trace-derived rows are candidates, not ground truth. Self-similarity labels
    are useful for drift detection, not human-verified correctness, so a person
    should confirm or fill the expected answers before those rows gate a
    release. This keeps regression data trustworthy as it grows.

The loop is the point: traces become datasets, datasets gate the next release,
and the agent keeps getting evaluated on the behavior that matters in
production.

## Try it

Confirm the signal is flowing, then turn real traces into regression coverage.

1. Check that AgentOps can reach Application Insights before you rely on the signal.

    ```bash
    agentops telemetry validate
    ```

2. Preview the traces and evaluation events AgentOps can currently see.

    ```bash
    agentops telemetry preview
    ```

3. Import a trace export so it can become regression coverage.

    ```bash
    agentops telemetry import
    ```

4. Promote reviewed production traces into regression dataset rows.

    ```bash
    agentops eval promote-traces --source .agentops/traces/export.jsonl
    ```

To browse this signal interactively and deep-link into Foundry and Azure
Monitor, run `agentops cockpit`. That local command center is covered on the
[Operate](operate.md#cockpit) page.

## Run from your coding agent

Install the AgentOps skills so your coding agent can read telemetry and grow the
regression set for you.

```bash
agentops skills install --platform copilot
```

The skills that map to observability are:

| Skill | What it helps with |
|---|---|
| `agentops-agent` | Watchdog analysis of production health and latency spikes. |
| `agentops-eval` | Promote traces and re-evaluate against the hardened dataset. |

## Next

Act on the signal over time on the [Operate](operate.md) page, feed passing
evidence back into the gate on the [Ship](ship.md) page, or harden the dataset
on the [Evaluation](evaluation.md) page.
