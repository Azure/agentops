# Tutorial — baseline comparison

Detect regressions between two AgentOps runs. This is the workflow
that turns AgentOps from a one-shot evaluation into a quality gate.

## The contract

```bash
agentops eval run --baseline <path-to-previous-results.json>
```

When `--baseline` is provided:

- Each metric is diffed against the baseline.
- The Markdown report grows a **Comparison vs Baseline** table with
  🟢 (improved) / 🔴 (regressed) / ⚪ (unchanged) markers.
- `results.json` includes a top-level `comparison:` block with the
  per-metric deltas, machine-readable.

The exit code still follows the threshold contract (`0` / `2` / `1`).
The baseline does **not** by itself fail the run — your thresholds in
`agentops.yaml` do.

## 1. Pick a baseline

Each `agentops eval run` writes to a timestamped folder under
`.agentops/results/` and refreshes `.agentops/results/latest/` with a
copy. So you have two options:

- **Local iteration** — point `--baseline` at
  `.agentops/results/latest/results.json`. AgentOps loads the baseline
  into memory before refreshing `latest/`, so it always means "the
  run before this one".
- **CI / shared baseline** — commit a stable copy into the repo (or
  publish it as a CI artifact). This is the path used by the
  `agentops-pr.yml` workflow:

  ```bash
  mkdir -p .agentops/baseline
  cp .agentops/results/latest/results.json .agentops/baseline/results.json
  git add .agentops/baseline/results.json
  git commit -m "chore: capture AgentOps baseline"
  ```

Use the first form while iterating; use the second when you want a
baseline that doesn't move every time someone runs `agentops eval run`
locally.

## 2. Make your change

Edit your prompt, swap the model deployment, change a tool — anything
you want to evaluate the impact of.

## 3. Re-run with `--baseline`

Local iteration (compares against the previous run):

```bash
agentops eval run --baseline .agentops/results/latest/results.json
```

Against a committed CI baseline:

```bash
agentops eval run --baseline .agentops/baseline/results.json
```

Open `.agentops/results/latest/report.md` in VS Code (`code .agentops/results/latest/report.md`, then `Ctrl+Shift+V` for the rendered preview). The new section looks like:

```markdown
## Comparison vs Baseline

| Metric              | Baseline | Current | Δ     |     |
|---------------------|----------|---------|-------|-----|
| coherence           | 4.20     | 4.45    | +0.25 | 🟢  |
| similarity          | 4.10     | 3.85    | -0.25 | 🔴  |
| avg_latency_seconds | 1.94     | 2.71    | +0.77 | 🔴  |
```

`Δ` direction is metric-aware: higher latency is bad, higher
similarity is good.

## 4. Wire into a PR check

The `agentops-pr.yml` workflow shipped by `agentops workflow generate`
already supports this — drop a baseline file in your repo (e.g.
`.agentops/baseline/results.json`) and add this step:

```yaml
- name: Run AgentOps eval against baseline
  run: |
    agentops eval run \
      --baseline .agentops/baseline/results.json
```

When a PR causes a metric to regress past your threshold, the run
exits `2` and the workflow fails, blocking merge until somebody
either fixes the regression or refreshes the baseline.

## 5. Refresh the baseline

When a regression is intentional (e.g. you swapped models on
purpose), promote the new run to the baseline:

```bash
cp .agentops/results/latest/results.json .agentops/baseline/results.json
git add .agentops/baseline/results.json
git commit -m "chore: refresh AgentOps baseline after model upgrade"
```

## See also

- [ci-github-actions.md](ci-github-actions.md) — full GenAIOps GitFlow with the four workflow templates
- [tutorial-quickstart.md](tutorial-quickstart.md) — the minimal AgentOps loop
