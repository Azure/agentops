# Tutorial: from POC to production-ready Foundry agents

This tutorial shows the production-readiness loop: eval, baseline, Doctor,
release evidence, CI/CD, and trace-backed regression rows.

## What AgentOps owns

AgentOps turns Foundry project signals into developer workflow:

1. `agentops eval analyze` checks whether local eval setup is ready.
2. `agentops eval run` produces `results.json` and `report.md`.
3. `agentops doctor --evidence-pack` writes release evidence.
4. `agentops workflow generate` puts the same gates into CI/CD.
5. `agentops eval promote-traces` creates regression candidates.

## 1. Start with a working eval

```powershell
agentops init
agentops eval analyze
agentops eval run
```

Capture the first known-good result as your release baseline:

```powershell
New-Item -ItemType Directory -Force .agentops\baseline | Out-Null
Copy-Item .agentops\results\latest\results.json .agentops\baseline\results.json
```

Run future candidates against that baseline:

```powershell
agentops eval run --baseline .agentops\baseline\results.json
```

## 2. Generate release evidence

```powershell
agentops doctor --workspace . --out .agentops\agent\report.md --evidence-pack
```

Outputs:

```text
.agentops/
├── agent/
│   └── report.md
└── release/
    └── latest/
        ├── evidence.json
        └── evidence.md
```

`evidence.json` is the stable contract (`version: 1`). `evidence.md` is the
reviewer-friendly summary. The top-level status is:

| Status | Meaning |
|---|---|
| `ready` | Required release signals are present and no warnings/blockers were found. |
| `ready_with_warnings` | The candidate can be reviewed, but gaps remain. |
| `blocked` | A release blocker exists, such as a missing or failing latest eval. |

This is a projection of existing signals, not a new exit-code contract. Eval
still returns `0`, `2`, or `1`; Doctor still gates by `--severity-fail`.

## 3. Put the same gates in CI/CD

Start with the safe PR gate:

```powershell
agentops workflow analyze
agentops workflow generate --kinds pr
```

After Azure OIDC, environments, and deploy steps are ready, generate the full
scaffold:

```powershell
agentops workflow generate --kinds pr,dev,qa,prod,watchdog --deploy-mode auto --force
```

Generated PR, production, and watchdog workflows run
`agentops doctor --evidence-pack` and upload release evidence. Production
deploys should still use environment approvals; AgentOps supplies the evidence,
not the human decision.

For AI Landing Zone / azd projects, keep deployment ownership in azd/Bicep:

```powershell
agentops workflow generate --deploy-mode azd --force
```

When `scripts/Invoke-PreflightChecks.ps1` exists, generated azd workflows run it
with `-Strict` before `azd provision`. Doctor reports the same landing-zone
readiness under Operational Excellence.

## 4. Turn production traces into regression candidates

Export relevant Foundry/App Insights traces to JSON or JSONL, then preview:

```powershell
agentops eval promote-traces --source .agentops\traces\candidate-traces.jsonl
```

Apply after reviewing the preview:

```powershell
agentops eval promote-traces --source .agentops\traces\candidate-traces.jsonl --apply
```

This writes:

```text
.agentops/data/
├── trace-regression.jsonl
└── trace-regression-manifest.json
```

The default `self-similarity` mode stores production responses as `expected`.
Use it for drift detection, not as proof that the answer was correct. Use
`--label-mode pending` when reviewers should fill expected answers before the
dataset becomes a blocking gate.

## 5. Production-readiness checklist

Before promoting a Foundry agent, you should be able to point to:

- Latest `agentops eval run` passed.
- Baseline comparison exists for regression decisions.
- `agentops doctor --evidence-pack` produced `evidence.md`.
- PR and production workflows upload AgentOps artifacts.
- Foundry continuous evaluation is enabled for runtime sampling.
- Application Insights / Azure Monitor is connected.
- AI Landing Zone preflight is wired when the repo uses that topology.
- Reviewed production traces are flowing back into a regression dataset.

When those items are visible, AgentOps has converted the POC into a repeatable
release path: quality gate, operational readiness, release evidence, and a
production feedback loop.
