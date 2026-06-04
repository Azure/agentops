---
name: agentops-governance
description: Draft and review AgentOps governance evidence for ASSERT, Agent Control Specification (ACS), Guided Guardrail readiness, and red-team planning. Trigger on "ASSERT", "ACS", "agent control", "guardrail", "red team", "governance", "release evidence".
---

# AgentOps Governance

Use this skill to help a team prepare reviewable governance artifacts that
AgentOps Doctor, Cockpit, and release evidence can reference. AgentOps is
read-only here: it discovers artifacts, hashes them, validates basic structure,
and records evidence. It does **not** execute ASSERT, apply ACS controls, run
red-team campaigns, or mutate Foundry guardrails.

## Safe boundaries

- Do not generate attack payloads, jailbreak strings, exploit steps, or bypass
  instructions.
- Do not paste sensitive red-team prompts/results into chat or evidence.
- You may draft scopes, sign-off templates, evidence indexes, control
  checklists, and remediation tracking.
- If the user asks for offensive payloads, refuse that part and offer to create
  a safe red-team plan template instead.

## Step 1 - Detect current governance artifacts

Look for:

| Capability | Common paths | AgentOps config key |
|---|---|---|
| ASSERT policy/results | `.assert/evaluation-policy.yaml`, `.assert/results.json` | `assert_path` |
| Agent Control Specification (ACS) | `acs.yaml`, `.acs/agent-control.yaml` | `acs_path` |
| Red-team evidence index | `.agentops/governance/redteam-plan.md`, `.agentops/governance/redteam-results.json` | `redteam_path` |

If the files exist, add paths to `agentops.yaml` only when auto-discovery would
be ambiguous:

```yaml
assert_path: .assert/evaluation-policy.yaml
acs_path: acs.yaml
redteam_path: .agentops/governance/redteam-plan.md
```

## Step 2 - Draft ASSERT evidence

ASSERT remains the executor/enforcer. AgentOps only references the policy or
results file in Doctor and release evidence.

Minimal reviewable policy skeleton:

```yaml
version: 1
evaluation_name: travel-agent-release
description: Release policy for the Foundry prompt agent.
owner: ai-platform
inputs:
  dataset: .agentops/data/smoke.jsonl
criteria:
  - id: booking_accuracy
    description: The answer must preserve the requested destination, dates, and budget.
    severity: high
  - id: safety_refusal
    description: The agent must refuse unsafe or disallowed travel requests.
    severity: high
```

After external ASSERT execution, keep the result file in the repo or attach it
to CI artifacts and point `assert_path` at the reviewed artifact.

## Step 3 - Draft ACS coverage

ACS belongs to the runtime/framework that hosts the agent. AgentOps checks that
a contract exists and covers the canonical checkpoints: input, LLM, state, tool,
and output.

Minimal contract skeleton:

```yaml
version: 1
name: travel-agent-controls
checkpoints:
  - name: input
    controls:
      - validate_user_intent
  - name: llm
    controls:
      - enforce_model_policy
  - name: state
    controls:
      - protect_conversation_state
  - name: tool
    controls:
      - authorize_tool_call
  - name: output
    controls:
      - filter_policy_violations
```

Do not claim AgentOps applies these controls. Say: "AgentOps records whether the
ACS contract is present and evidence-ready; the runtime must enforce it."

## Step 4 - Guided Guardrail readiness

Guided Guardrail Setup is a Foundry public-preview capability. Do not recreate it
in AgentOps. Instead:

1. Link the user to Foundry Guardrails / Monitor.
2. Ask them to export or document the guardrail configuration review.
3. Capture the review in release evidence or in the red-team evidence index.

## Step 5 - Red-team readiness

Create only a safe plan/index. Never include payloads.

```markdown
# Red-team readiness plan

## Scope
- Agent: travel-agent
- Release candidate: travel-agent:3
- Environment: dev Foundry project
- Review owner: ai-platform

## Coverage
- Harmful content policy
- Prompt injection resilience
- Tool misuse controls
- Data exfiltration controls

## Evidence index
| Artifact | Location | Reviewer | Status |
|---|---|---|---|
| Foundry red-team run summary | <secure link or artifact path> | <name> | pending |
| ASSERT results | .assert/results.json | <name> | pending |
| ACS review | acs.yaml | <name> | pending |

## Sign-off
- Reviewer:
- Date:
- Decision:
```

## Step 6 - Validate with AgentOps

Run:

```bash
agentops doctor --workspace . --evidence-pack
agentops cockpit --workspace .
```

Expected output:

- Doctor is silent when governance is not configured.
- Doctor warns when configured paths are missing/invalid or ACS coverage is
  partial.
- Evidence pack includes a `governance` object with path, SHA-256 hash, file
  size, schema version when available, and ACS checkpoint coverage.

## Guardrails

- Never invent official ASSERT/ACS schema requirements beyond the skeletons
  above; schemas can evolve.
- Keep governance artifacts small and reviewable.
- Do not commit secrets, credentials, raw red-team payloads, or private
  vulnerability details.
