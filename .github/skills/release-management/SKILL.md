---
name: release-management
description: 'Guide maintainers and contributors through branching, versioning, changelog updates, and publishing agentops-accelerator. Trigger when users ask about branching strategy, creating a release, version tagging, publishing to PyPI, updating the changelog, cutting a release, opening a PR, or syncing a fork. Common phrases include "cut a release", "how do I publish", "create release branch", "tag a version", "update changelog", "release process", "bump version", "what branch should I use", "feature branch", "prepare release".'
---

# Release Management

## Purpose
Guide contributors and maintainers through the AgentOps branching strategy, versioning conventions, changelog lifecycle, and PyPI release process.

## When to Use
- User asks what branch to base work on or where to raise a PR.
- User asks how to create a feature or release branch.
- User asks how to prepare a release or cut a version.
- User asks how to update the changelog.
- User asks how to tag a version or publish to PyPI.
- User asks how to sync their fork after a release.
- Instructions about branching or versioning are ambiguous.

## Branching Model

| Branch | Purpose |
|---|---|
| `main` | Always stable and deployment-ready. Only receives merges from `release/vx.y.z` branches. |
| `develop` | Integration branch. All feature PRs target here. |
| `release/vx.y.z` | Created by maintainers from `develop` when a release is ready to ship. |
| `feature/<name>` | Created by contributors from `develop` for all new work. |

**Default rule:** unless explicitly told otherwise, all work starts from `develop`.

## Feature Development Workflow

### Branch naming
```
feature/<short-description>
```
Examples: `feature/conversation-metadata`, `feature/add-evaluation-logging`

### Flow
1. Start from `develop`
2. Create `feature/<name>`
3. Implement changes
4. Commit with [conventional commit messages](#commit-guidelines)
5. Open PR → `develop`

### PR contract
- Source: `feature/*`
- Target: `develop`
- Never open a feature PR directly to `main`

## Release Workflow (Maintainers)

### Release branch naming
```
release/vx.y.z
```
Examples: `release/v2.4.2`, `release/v0.2.0`

### Flow

**Preferred: One-click via Cut Release workflow**
1. Confirm `develop` is green (CI passes) and all intended changes are merged.
2. Go to **Actions** tab → **Cut Release** → **Run workflow** → enter version (e.g. `0.2.0`, no `v` prefix).
3. The workflow automatically:
   - Creates `release/v0.2.0` from `develop`
   - Updates `CHANGELOG.md` (adds versioned section `[0.2.0] - YYYY-MM-DD`)
   - Pushes the branch (triggers staging pipeline automatically)
   - Opens a PR: `release/v0.2.0` → `main`
4. Wait for staging pipeline to pass (build → TestPyPI → verify), and review
   the separately protected `marketplace-staging` deployment for the legitimate
   Marketplace pre-release. The branch push is a real publication attempt.
5. Get the PR reviewed and merge into `main`.
6. Tag the release on `main` **and sync `develop` in the same sitting**. Tagging
   publishes to PyPI immediately; there is no approval prompt. Leaving `develop`
   behind `main` corrupts the next release's CHANGELOG.
   ```bash
   git checkout main
   git pull origin main
   git tag v0.2.0
   git push origin v0.2.0

   git checkout develop
   git pull origin develop
   git merge main
   git push origin develop

   # MUST print nothing:
   git fetch origin && git log --oneline origin/develop..origin/main
   ```
   After the merge, open `CHANGELOG.md` and confirm everything under
   `## [Unreleased]` is genuinely unreleased. Git happily places the incoming
   `## [0.2.0]` heading above develop's unreleased entries, nesting new work
   inside a shipped version.
7. Watch the Release workflow finish (build → TestPyPI → verify → publish-pypi →
   github-release). The Python `release` environment has no protection rules,
   so PyPI does not pause for review. The separate `marketplace-release`
   deployment requires review for the stable extension publication.
8. Delete the release branch:
   ```bash
   git push origin --delete release/v0.2.0
   git branch -d release/v0.2.0
   ```

**Alternative: Manual release branch creation**
1. Confirm `develop` is green.
2. Create release branch from `develop`:
   ```bash
   git checkout develop
   git pull origin develop
   git checkout -b release/v0.2.0
   ```
3. Update `CHANGELOG.md` - see [Changelog Lifecycle](#changelog-lifecycle) below.
4. Commit and push:
   ```bash
   git add CHANGELOG.md
   git commit -m "chore: prepare release 0.2.0"
   git push origin release/v0.2.0
   ```
   This triggers the staging pipeline automatically.
5. Open PR: `release/v0.2.0` → `main`.
6. After staging passes and review is complete, merge to `main`.
7. Tag and push, then immediately sync `develop` and verify with
   `git log --oneline origin/develop..origin/main` (must be empty). Pushing the
   tag publishes to PyPI with no approval prompt. Same commands and same
   CHANGELOG check as step 6 above.
   ```bash
   git checkout main
   git pull origin main
   git tag v0.2.0
   git push origin v0.2.0
   ```
8. Delete the release branch (same as above).

### Release PR contract
- Source: `release/vx.y.z`
- Target: `main`
- Do NOT introduce new feature work in a release branch - only changelog updates.

## Versioning Rules

Follow [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`

| Type | When to use |
|---|---|
| `PATCH` | Bug fixes and minor backward-compatible improvements |
| `MINOR` | New backward-compatible features |
| `MAJOR` | Breaking changes to the CLI contract or output schema |

Version numbers follow a consistent pattern across artifacts. The git tag and GitHub Release use a `v` prefix. The release branch also uses the `v` prefix. Versioning is fully automatic via **setuptools-scm** - there is no `version` field in `pyproject.toml`.

| Artifact | Format | Example |
|---|---|---|
| Release branch | `release/vx.y.z` | `release/v2.4.2` |
| `pyproject.toml` | `dynamic = ["version"]` | Version derived from git tags via setuptools-scm |
| Git tag / GitHub Release | `vx.y.z` | `v2.4.2` |
| Changelog heading | `## [x.y.z] - YYYY-MM-DD` | `## [2.4.2] - 2026-03-22` |

**Never add `version = "..."` to `pyproject.toml`** - this will conflict with setuptools-scm.

### Version on `develop`
- The version on `develop` is derived automatically by setuptools-scm (e.g., `0.1.3.dev12`).
- Do NOT preemptively bump any version on `develop` for an upcoming release.
- Feature branches should not modify `pyproject.toml` version.

## Changelog Lifecycle

The changelog follows a two-phase lifecycle: development on `develop`, finalization on `release/vx.y.z`.

### Development phase (`develop`)
- Add all user-visible changes under the next versioned section at the top of the changelog.
- Do NOT preemptively assign a future version number on `develop`.
- Do NOT create empty version sections.

```markdown
## [0.2.0] - 2026-04-20

### Added
- New orchestration strategy for multi-turn evaluations.

### Fixed
- Corrected resource cleanup order in Foundry backend shutdown.
```

### Release phase (`release/vx.y.z`)
When creating the release branch, the cut-release workflow inserts a versioned section header with the release date. Verify the changelog entries are correct and complete.

All release artifacts must be in sync:

| Artifact | Value |
|---|---|
| Release branch | `release/v2.4.2` |
| Changelog heading | `## [2.4.2] - YYYY-MM-DD` |
| Git tag / GitHub Release | `v2.4.2` |

### Changelog sections
Use when applicable: `Added`, `Changed`, `Fixed`, `Removed`, `Deprecated`, `Security`.

### Writing style
- Start each entry with a **bold title**, followed by a brief technical explanation.
- Explain what changed and why it matters - include relevant technical context.
- Avoid vague wording: no "minor updates", "improvements", or "fixes" as standalone entries.

### Safety rules
- Never assign a release version on `develop` prematurely.
- Never leave a release branch without a properly dated versioned entry.
- Never mismatch version numbers across branch name, changelog, and tag.
- Never leave `develop` behind `main` after a release. `cut-release.yml` branches
  from `develop` and replaces the `## [Unreleased]` marker exactly once, so a
  stale `develop` republishes shipped entries and drops the previous version's
  section when the next release PR merges into `main`.

## Commit Guidelines

Use conventional commit format:

```
feat: add conversation metadata support
fix: correct chat history persistence issue
docs: update changelog for 2.4.2
chore: prepare release 2.4.2
```

## Publishing Authentication

Both `staging.yml` and `release.yml` publish through
[PyPI Trusted Publishing](https://docs.pypi.org/trusted-publishers/) using
`id-token: write`. There is no PyPI or TestPyPI API token stored in this repo.

| Secret | Scope | Purpose |
|---|---|---|
| `RELEASE_PAT` | repository | Used by `cut-release.yml` to open the release PR |

Neither the `staging` nor the `release` environment holds any secret. Confirm with
`gh api repos/Azure/agentops/actions/secrets --jq '.secrets[].name'`.

Trusted Publishing is configured on the index side (pypi.org and test.pypi.org →
**Manage → Publishing**) and must match the repository, workflow filename, and
environment name exactly. A mismatch fails with `403` at upload time.

### Marketplace: dedicated Entra OIDC identity

Marketplace jobs use a **dedicated user-assigned managed identity (UAMI)**,
`azure/login@v3` with `allow-no-subscriptions: true`, and `id-token: write`.
No Azure RBAC grant is needed solely to publish. Publisher membership supplies
that permission. Do not change Python Trusted Publishing, shared `AZURE_*` E2E
variables, repository-wide OIDC configuration, or the GitHub `RELEASE_PAT`.

Use two **new, separate** GitHub environments, not Python `staging`/`release`.
Configure required reviewers and selected branch/tag deployment policies
**before setting variables**:

| Environment | Allowed deployment refs |
| --- | --- |
| `marketplace-staging` | Branches `release/*` for legitimate `release/vX.Y.Z` candidates |
| `marketplace-release` | Tags `v*`; optionally protected `main` for manual dispatch with tag input |

Stable manual job guards allow only `main` or the same release tag as the input.
An environment subject alone does not restrict branches; protections are essential.
For an authorized pre-migration-tag retry, explicitly dispatch the **new migrated
workflow on protected `main`** with a valid release tag. The Marketplace job
checks out tooling from `github.workflow_sha` at the workspace root and extension
source from `refs/tags/<tag>` into `release-source/`: old extension source uses
the new OIDC action/helper. Tag-based dispatch must match the tag input.
Re-running a historical old workflow still executes its old PAT code, not the
migrated workflow. A retry is a real publication attempt.

Each new environment needs these variables:

- `MARKETPLACE_AZURE_CLIENT_ID`: UAMI client GUID.
- `MARKETPLACE_AZURE_TENANT_ID`: approved identity tenant GUID.
- `MARKETPLACE_PROFILE_ID`: Marketplace `profiles/me` profile `id`,
  **not** the Entra principal/object ID.

Verify customization read-only with
`gh api repos/Azure/agentops/actions/oidc/customization/sub`. The verified ordered
claim keys are `repository_owner_id`, `repository_id`, `context` with
`use_default: false`. Federation must use:

- Issuer: `https://token.actions.githubusercontent.com`
- Audience: `api://AzureADTokenExchange`
- Staging subject: `repository_owner_id:6844498:repository_id:1161883340:environment:marketplace-staging`
- Release subject: `repository_owner_id:6844498:repository_id:1161883340:environment:marketplace-release`

Resolve the UAMI Marketplace profile using a CLI token for resource
`499b84ac-1321-427f-aa17-267ca6975798` and a read-only request to
`https://app.vssps.visualstudio.com/_apis/profile/profiles/me?api-version=7.1`.
Record only the profile `id`; never print or store token values. A publisher
Owner must grant that profile **Contributor, not Owner**, on `AgentOpsAccelerator`.

### Shared helper and local publishing

- Requires Python 3.11+ (stdlib), Azure CLI, and Node 22 with `vsce >=3.9.2`
  for publishing. Workflows pin `npm install -g @vscode/vsce@3.9.2`.
- `python scripts/marketplace.py check` is read-only: it selects
  `MARKETPLACE_AZURE_TENANT_ID`, pins the self profile to
  `MARKETPLACE_PROFILE_ID`, and checks explicit Contributor/Owner/Creator
  membership with zero deny permissions. A generic HTTP 200 is not sufficient.
- `python scripts/marketplace.py publish --package-path PATH [--pre-release] [--allow-already-exists]`
  preflights before uploading. CI opts into already-existing-version handling;
  local defaults fail. The flag maps to native `vsce --skip-duplicate`
  (existing-version/409 only), not output substring matching; other errors
  always propagate. The child environment clears inherited PAT and
  EnvironmentCredential variables and selects the tenant. **No PAT fallback.**
- Local staging/release scripts preflight before side effects when `vsce`
  exists, but then publish. Their prior missing-`vsce` extension-packaging skip
  remains unchanged and does not prove Marketplace access.
  Use `check` alone for no-upload validation. First run
  `az login --tenant <publisher-identity-tenant> --allow-no-subscriptions` and set
  `MARKETPLACE_AZURE_TENANT_ID` and `MARKETPLACE_PROFILE_ID` for **your interactive
  publishing identity**, not the UAMI. Your identity needs publisher Contributor
  or Owner membership. Profile pinning prevents wrong account/tenant publishing.
- Extension `npm run publish` / `npm run publish:prerelease` package a VSIX
  before calling the shared helper, which preflights before upload. They require
  Python 3.11+ and the helper from the repository checkout.
- CLI profile and role preflight success is **not proof of actual upload**.

### Staged rollout (not completed by merging code)

1. Obtain approval for permanent ownership and production tenant/subscription
   placement outside code rollout. The earlier non-production
   personal-subscription probe is feasibility evidence, not policy approval.
2. Configure the dedicated UAMI, exact federation, publisher Contributor
   membership, environment reviewers and deployment policies, then variables.
3. Run an authorized permission-only preflight (`check`, no publishing scripts).
   No standalone read-only workflow is added by this change. Arrange approved
   OIDC permission validation before the first release; a local interactive
   `check` alone does not validate CI federation.
4. Explicitly authorize and verify a **legitimate** Marketplace pre-release.
5. Explicitly authorize and verify a **legitimate** stable publication.
6. **Only then** remove the legacy GitHub `VSCE_PAT`. Its owner must confirm no
   other consumers before revoking the underlying Azure DevOps PAT.
   Include historical workflow re-runs in that review; retire old PAT paths
   and use the migrated workflow on `main` for authorized old-tag retries.
   **Never touch `RELEASE_PAT`.**

[Prior successful no-upload proof](https://github.com/Azure/agentops/actions/runs/34046045685/attempts/3)
used temporary resources, all since deleted. Do not claim permanent resources,
actual publication, or legacy-secret removal are complete based on that proof.
See [the release guide](../../../docs/release-process.md#104-marketplace-entra-oidc-identity-setup)
for setup and rollout details.

## Default Decision Logic

| Situation | Action |
|---|---|
| Feature or code change | Base on `develop`, create `feature/*`, PR to `develop` |
| Release preparation | Base on `develop`, create `release/x.y.z`, update `pyproject.toml` + `CHANGELOG.md`, PR to `main` |
| Ambiguous instructions | Default to feature workflow on `develop`; do not assume a release unless explicitly requested |

## Guardrails
- Never create feature branches from `main`.
- Never open feature PRs to `main`.
- Never mix new feature work into a release branch.
- Never assign a release version on `develop`.
- Never tag without a green CI run.
- Never treat the tag push as reversible. It publishes to PyPI with no approval
  prompt, and PyPI versions can only be yanked, never replaced.
- Never end a release without running `git log --oneline origin/develop..origin/main`
  and seeing empty output.
- Never publish without running `python -m pytest tests/ -x -q` first.
- Never treat release workflows as dry runs or create dummy release branches,
  tags, or production versions to test them. Staging includes a real Marketplace
  pre-release attempt. Use local packaging and standalone read-only preflight
  instead; Marketplace approvals do not pause Python publishing.
