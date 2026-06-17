# Own

This page is about owning an agent over time, not just shipping it once.
Ownership is the loop of scoring readiness, proving the ship decision with
evidence, and feeding production learning back into the next evaluation. The
Doctor and the evidence pack are the two tools that make that loop concrete.

For the full check inventory, see the [Doctor checks reference](doctor-checks.md).
For a narrative walkthrough of what the Doctor is and how it reasons, see
[The Doctor, explained](doctor-explained.md).

## Doctor as the readiness scorer

The Doctor is a regular check-up for an agent project. It reads signals that are
already there, eval history, App Insights telemetry, Foundry metadata, and Azure
resource configuration, and emits **findings**: severity-ranked observations
with a recommendation attached.

It does not fix anything and it does not replace Foundry's compliance surface. It
is the complementary half that scores runtime telemetry, identity scope, eval
discipline, and pipeline hygiene.

```
agentops doctor
```

!!! info "Findings, severities, and exit codes"
    Findings are grouped into categories like quality, performance, reliability,
    security, responsible AI, and operational excellence. Severity is
    independent of category, so a quality finding can be critical, warning, or
    info. The Doctor exits `0` when nothing meets the configured
    `--severity-fail` floor, `2` when something does, and `1` if the analyzer
    itself errored.

## The evidence pack as ship/no-ship proof

Adding `--evidence-pack` turns a Doctor run into a release decision artifact:

```bash
agentops doctor --evidence-pack
```

This writes `.agentops/release/latest/evidence.json` and `evidence.md`. The
evidence pack projects signals you already produce, eval results, baselines,
Doctor findings, workflow files, Foundry continuous-eval, monitoring, and
trace-regression manifests, into one readiness summary.

| Artifact | Use it for |
|---|---|
| `evidence.json` | The stable machine-readable contract (`version: 1`) for automation. |
| `evidence.md` | The PR and release-review summary, including the Doctor finding rollup. |

!!! note "Evidence does not add a new gate"
    The readiness states `ready`, `ready_with_warnings`, and `blocked` are
    projections of existing signals. They do not create a second exit-code
    contract: eval and Doctor exit codes stay exactly as they are. A `blocked`
    status tells a reviewer to stop; the underlying Doctor exit code still
    depends only on `--severity-fail`.

## Release readiness

Release readiness is the question the evidence pack answers: is there current,
passing eval evidence, a baseline to judge regressions against, promoted
production traces where they exist, and continuous evaluation wired up. The
Doctor emits operational-excellence findings for each of these so gaps are
visible before a release review, not after.

Generated production workflows append the evidence report to the run summary, so
when a release blocks you can start from the critical and warning finding ids
before opening the full artifact.

## The ownership loop

Owning an agent means running this loop, not a one-time checklist.

```mermaid
flowchart LR
    M["Monitor<br/>traces + telemetry"] --> R["Regress<br/>promote traces to dataset"]
    R --> E["Re-evaluate<br/>eval run + Doctor"]
    E --> P["Prove<br/>evidence pack"]
    P --> M
```

You monitor production behavior, promote reviewed traces into regression rows,
re-evaluate against the hardened dataset, and produce fresh evidence for the next
decision. Each pass makes the gate reflect more of what the agent actually does.

To see the monitoring half of this loop in depth, read [Observe](observe.md).
To see how the gate runs in CI, read [Ship](ship.md).
