# Quickstart: Validate Regression Commit Attribution

## Prerequisites

- A local clone of this repo on the `012-regression-commit-attribution`
  branch, with the feature implemented per `tasks.md` (not yet generated at
  plan time).
- Python 3.11+, repo dev dependencies installed (`pip install -e ".[dev]"` or
  the project's usual dev setup).
- A minimal `agentops.yaml` pointing at any locally runnable target (a
  generic HTTP/JSON echo agent is enough — commit attribution does not
  require a real Foundry hosted agent to validate the mechanism end to end;
  a hosted-agent target should additionally be spot-checked in CI per
  scenario 2 below).

## Scenario 1: Local best-effort commit capture (User Story 3)

1. Inside a git-initialized workspace, make a commit, then run:
   ```bash
   agentops eval run --config agentops.yaml
   ```
2. Inspect the produced result:
   ```bash
   cat .agentops/results/latest/results.json | python -m json.tool | grep -A5 '"commit"'
   ```
   **Expected**: a non-null `commit` object with `source: "local"` and a
   `sha` matching `git rev-parse HEAD`.
3. Re-run the same command from a plain (non-git) temp directory copy of the
   workspace.
   **Expected**: the run completes normally, exit code unchanged, and
   `commit` is `null` in the result — no error, no warning that blocks the
   run.

## Scenario 2: Regression explanation across two commits (User Story 1)

1. Run the evaluation once, then change the agent's configured model or
   prompt version in `agentops.yaml` in a way expected to lower a metric,
   commit that change, and run the evaluation again with the committed
   baseline wired up:
   ```bash
   mkdir -p .agentops/baseline
   cp .agentops/results/latest/results.json .agentops/baseline/results.json
   git add .agentops/baseline/results.json && git commit -m "chore: promote eval baseline"
   # ...make the prompt/model change, commit it...
   agentops eval run --config agentops.yaml --baseline .agentops/baseline/results.json
   ```
2. Open `.agentops/results/latest/report.md`.
   **Expected**: a "Regression Insight" section naming the regressed metric's
   before/after values and the changed input(s) (prompt/model/config),
   matching the shape in `contracts/report-and-cockpit.md`.
3. Confirm the exit code is unchanged from before this feature (i.e. driven
   only by configured thresholds, not by the presence of an insight):
   ```bash
   echo $?
   ```

## Scenario 3: Graceful fallback with no commit metadata

1. Manually strip the `commit` field from one of the two `results.json` files
   used above (or use a baseline captured before this feature existed).
2. Re-run the comparison.
   **Expected**: `report.md` still renders the existing "Comparison vs
   Baseline" table with correct metric deltas, but **no** "Regression
   Insight" section — no fabricated cause, no error.

## Scenario 4: Cockpit version history (User Story 2)

1. Produce at least three local runs for the same agent/dataset/evaluator
   combination (varying the config between some of them).
2. Start Cockpit:
   ```bash
   agentops cockpit
   ```
3. Open the dashboard and locate the version history view.
   **Expected**: every run appears in order, each showing its commit
   reference (or none, if unknown) and what changed relative to the previous
   entry for that methodology — including runs where nothing regressed.

## Automated coverage (to be added under `tasks.md`)

- `tests/unit/test_commit_info.py` — env-var precedence, local `git`
  fallback, missing-git/non-repo handling.
- `tests/unit/test_regression_insight.py` — field-diff detection (prompt,
  model, dataset, evaluators, thresholds), the "both commits missing" and
  "one commit missing" no-fabrication paths, and the `used_git_diff`
  fallback branch.
- `tests/unit/test_reporter.py` — new "Regression Insight" section rendering,
  and its absence when `insight` is `None`.
- `tests/unit/test_cockpit.py` — `_project_run` commit/`changed_inputs`
  projection and the new history payload shape.
- `tests/integration/` — an end-to-end two-run scenario (mirroring Scenario
  2 above) asserting the rendered `report.md` text and unchanged exit code.
