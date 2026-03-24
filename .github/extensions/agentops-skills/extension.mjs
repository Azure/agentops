// Extension: agentops-skills
// Injects AgentOps workflow skills as context when relevant prompts are detected.

import { joinSession } from "@github/copilot-sdk/extension";

const SKILLS = {
    "run-evals": {
        keywords: [
            "run eval", "start agentops", "run.yaml", "regenerate report",
            "evaluation results", "agentops init", "agentops eval", "agentops report",
            "run an evaluation", "initialize agentops", "results.json", "report.md",
            "eval run", "run config", "evaluation output",
        ],
        context: `## Skill: Run Evaluations

### Purpose
Guide through the implemented AgentOps evaluation workflow from workspace setup to report interpretation.

### Available Commands
- agentops init [--path <dir>] — Initialize workspace
- agentops eval run — Execute evaluation
- agentops report — Regenerate report from results.json

### Typical Workflow
1. Initialize workspace: agentops init
2. Confirm run config exists (.agentops/run.yaml)
3. Execute evaluation: agentops eval run
4. Regenerate markdown report: agentops report
5. Inspect outputs under .agentops/results/latest/

### Outputs
- results.json (machine-readable normalized results)
- report.md (human-readable summary)
- cloud_evaluation.json (cloud evaluation flows only)
- Latest pointers: .agentops/results/latest/

### Interpretation
- Start with report.md for quick pass/fail narrative and threshold view.
- Use results.json for metric-level details, row-level checks, and automation.
- Distinguish: thresholds passing, threshold failures, runtime/config errors.

### Guardrails
- Do not invent commands or flags beyond documented CLI behavior.
- Planned commands (compare, run-history) are stubbed — pivot to artifact inspection.`,
    },

    "investigate-regression": {
        keywords: [
            "regression", "score dropped", "threshold started failing",
            "compare runs", "eval got worse", "debug evaluation",
            "evaluation drift", "quality drop", "pass rate dropped",
            "ci failing", "scores lower", "metrics degraded",
        ],
        context: `## Skill: Investigate Regression

### Purpose
Guide through regression investigation using currently available AgentOps outputs.

### Available Commands
- agentops eval run — Generate fresh artifacts
- agentops report — Regenerate report

### Planned (not implemented)
- agentops eval compare --runs ID1,ID2

### Investigation Steps
1. Run fresh evaluation: agentops eval run
2. Regenerate report: agentops report
3. Compare current artifacts to baseline manually
4. Report factual deltas, then propose controlled next steps

### Required Inputs
- At least one recent artifact set (results.json + report.md)
- Preferably a baseline for side-by-side comparison
- Context about what changed (prompt, model, dataset, bundle, backend, environment)

### Interpretation
- Separate observations (artifact-backed) from hypotheses (plausible causes).
- Prioritize impact: which thresholds flipped, which metrics degraded most, broad vs concentrated failures.
- End with actionable next checks (rerun with controlled changes, validate dataset, verify config).

### Guardrails
- agentops eval compare is NOT implemented — use manual artifact comparison.
- Do not infer causality from correlation alone.
- Keep remediation tied to reproducible checks.`,
    },

    "observability-triage": {
        keywords: [
            "tracing", "monitoring", "dashboard", "alerts", "triage",
            "observability", "run health", "production triage",
            "monitor evals", "set up tracing", "failed evaluation",
            "quality monitoring",
        ],
        context: `## Skill: Observability Triage

### Purpose
Provide honest observability guidance: use current reporting artifacts today, frame tracing/monitoring as planned future work.

### Available Commands (for triage today)
- agentops eval run
- agentops report

### Planned/Stubbed (NOT implemented)
- agentops trace init
- agentops monitor setup
- agentops monitor dashboard
- agentops monitor alert

### Current Triage Approach
- Use report.md for quick operational triage (what failed, severity).
- Use results.json for detailed metric and threshold inspection.
- Keep run artifacts organized for future compare/monitor automation.

### When Users Ask for Unimplemented Features
1. State explicitly: planned/stubbed, not available yet.
2. Provide immediate fallback: artifact-based troubleshooting.
3. Suggest preparation: organize artifacts for future tooling.

### Guardrails
- Do not present tracing or monitoring commands as available.
- Do not imply real-time dashboards/alerts exist in CLI.
- Always pivot to concrete available outputs (results.json, report.md).`,
    },
};

function matchSkills(prompt) {
    const lower = prompt.toLowerCase();
    const matched = [];
    for (const [name, skill] of Object.entries(SKILLS)) {
        if (skill.keywords.some((kw) => lower.includes(kw))) {
            matched.push(skill.context);
        }
    }
    return matched;
}

const session = await joinSession({
    hooks: {
        onUserPromptSubmitted: async (input) => {
            const matched = matchSkills(input.prompt);
            if (matched.length > 0) {
                return {
                    additionalContext: `<agentops_skills>\n${matched.join("\n\n---\n\n")}\n</agentops_skills>`,
                };
            }
        },
    },
});
