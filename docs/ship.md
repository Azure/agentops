# Ship

This page explains how AgentOps gates a release in CI/CD. AgentOps owns the
repo-side quality gate; your platform owns infrastructure and deployment. The
goal is simple: the agent version that gets deployed is the exact version that
was evaluated.

For the full GitHub Actions reference, including every workflow file, the YAML,
and the OIDC and RBAC setup steps, see
[AgentOps on GitHub Actions](ci-github-actions.md). This page is the overview
that explains why the pieces fit together.

## How branches map to environments

AgentOps assumes a GitFlow-style branch model. PRs run an eval against the
candidate agent before merge. Deploy workflows run after merge and promote the
reviewed branch to its environment:

```mermaid
flowchart LR
    feature["feature/*"] --> prDev["PR eval<br/>candidate"]
    prDev --> develop["develop"]
    develop --> devDeploy["Eval + deploy<br/>agentops-deploy-dev"]
    devDeploy --> devEnv["dev"]

    develop --> release["release/*"]
    release --> qaDeploy["Eval + deploy<br/>agentops-deploy-qa"]
    qaDeploy --> qaEnv["qa"]

    release --> prProd["PR eval<br/>candidate"]
    prProd --> main["main"]
    main --> prodDeploy["Safety eval + deploy<br/>agentops-deploy-prod"]
    prodDeploy --> prodEnv["production<br/>required reviewers"]

    classDef branch fill:#e7f0fd,stroke:#1f4e79,color:#000;
    classDef pipeline fill:#ede7f6,stroke:#4527a0,color:#000;
    classDef env fill:#d1ecf1,stroke:#0c5460,color:#000;
    class feature,develop,release,main branch;
    class prDev,devDeploy,qaDeploy,prProd,prodDeploy pipeline;
    class devEnv,qaEnv,prodEnv env;
```

The PR gate (`agentops-pr.yml`) guards every merge. It does not validate the
already-deployed dev app. In the HTTP tutorial, it evaluates the sandbox
endpoint. In prompt-agent flows, it evaluates a staged candidate prompt. A
per-environment deploy workflow promotes `develop` to dev, `release/**` to QA,
and `main` to prod. The two you start with are covered next; the full set, with
the workflow YAML and the GitHub Environment and OIDC setup, is in
[AgentOps on GitHub Actions](ci-github-actions.md).

## The two core workflows

A generated AgentOps scaffold ships a PR gate and per-environment deploy
workflows. The two that matter most early are the PR gate and the dev deploy.

| Workflow | Trigger | What it does |
|---|---|---|
| `agentops-pr.yml` | PRs to `develop`, `release/**`, `main` | Evaluates the PR candidate, runs the Doctor gate, and comments on the PR. |
| `agentops-deploy-dev.yml` | push to `develop` | Evaluates, then builds and deploys to the dev environment. |

You do not write these by hand. `agentops workflow analyze` reads the repo and
recommends the deploy wiring and eval runner, and `agentops workflow generate`
writes the workflow files from that recommendation. Because both use the same
analysis, the plan and the generated files do not drift.

!!! note "Generate the PR gate first"
    Start with `agentops workflow generate --kinds pr`. Add the dev, qa, and
    prod deploys only after GitHub Environments and Azure OIDC are ready. This
    keeps your first green run small and avoids wiring deploy steps before the
    gate works.

## Candidate versioning in Foundry

For Foundry prompt agents, each deploy stages a **candidate version** from your
source-controlled `prompt_file` before evaluating it. PR-run candidates are
tagged in Foundry with `agentops:candidate=true` plus the PR number and a
timestamp, so portal viewers can tell abandoned PR candidates apart from
deployed versions of record.

The deployed-of-record version is tracked in `foundry-agent.json`, written per
environment as a workflow artifact after the gate passes. That file, not the
Foundry version number, is the supported way to know what each environment runs.

!!! info "Prompt SHA and git SHA are the durable identity"
    Foundry version numbers are local to each project, so sandbox
    `travel-agent:2` may not match the dev or prod number. AgentOps instead
    records `agentops.prompt_sha256` (the prompt text) and `agentops.git_sha`
    (the commit) on every version and in `foundry-agent.json`. To check whether
    two environments run the same prompt, compare those SHAs, not the version
    numbers.

This is the invariant the whole flow protects: the evaluated agent version is
the deployed agent version. Foundry manages the candidate versions; AgentOps
supplies the gate, the deployment record, and Cockpit visibility.

## Why the PR gate evaluates against dev, not sandbox

The PR gate stages and evaluates its candidate in the **dev** project, the same
target the dev deploy will use. Sandbox is the author's playground and never
receives CI traffic, so evaluating there would gate a different environment than
the one you ship to.

Evaluating against dev means the gate result reflects the real deploy target.
A passing PR gate is evidence that the same candidate will behave the same way
when the dev deploy promotes it.

## The Doctor gate in CI

Every PR run also runs `agentops doctor --evidence-pack` after the eval step.
The eval step is the hard merge gate; the Doctor gate adds readiness checks like
regression detection against the rolling baseline.

| `--doctor-gate` value | PR behavior |
|---|---|
| `critical` (default) | Blocks the PR on critical findings, including a regression that drops a metric well below baseline even when eval thresholds still pass. |
| `warning` | Also blocks on smaller regression drops. |
| `none` | Doctor still writes and uploads evidence, but does not block; the eval step stays the only hard gate. |

Production deploy templates always run Doctor with a critical finding gate. To
understand the findings and severities behind these gates, see
[Own](own.md) and the [Doctor checks reference](doctor-checks.md).

## Identity and access

CI authenticates to Azure with GitHub OIDC and federated credentials, so no
long-lived secrets live in the repo. The same principal needs the right Azure
RBAC roles before the first run, or the eval step fails with null metrics.

!!! warning "Two roles are required for prompt-agent gates"
    The OIDC principal needs **Foundry User** on the Foundry project and
    **Cognitive Services OpenAI User** on the AI Services account that hosts the
    judge model. If only one is in place, every metric returns `null` and the
    gate fails without an obvious cause. The full setup, including the role ids
    and the federation steps, is in [AgentOps on GitHub Actions](ci-github-actions.md).

For the underlying procedures, follow Microsoft's own docs rather than copying
long steps here:

- [GitHub OIDC with Azure (workload identity federation)](https://learn.microsoft.com/azure/active-directory/workload-identities/workload-identity-federation-create-trust?pivots=identity-wif-apps-methods-azp)
- [Assign Azure roles (RBAC)](https://learn.microsoft.com/azure/role-based-access-control/role-assignments-portal)
