---
name: release-management
description: Guide maintainers and contributors through branching, versioning, changelog updates, and publishing agentops-toolkit. Trigger when users ask about branching strategy, creating a release, version tagging, publishing to PyPI, updating the changelog, cutting a release, opening a PR, or syncing a fork. Common phrases: "cut a release", "how do I publish", "create release branch", "tag a version", "update changelog", "release process", "bump version", "what branch should I use", "feature branch", "prepare release".
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
   - Updates `CHANGELOG.md` (`[Unreleased]` → `[0.2.0] - YYYY-MM-DD`)
   - Pushes the branch (triggers staging pipeline automatically)
   - Opens a PR: `release/v0.2.0` → `main`
4. Wait for staging pipeline to pass (build → TestPyPI → verify).
5. Get the PR reviewed and merge into `main`.
6. Tag the release on `main` — this triggers the production release pipeline:
   ```bash
   git checkout main
   git pull origin main
   git tag v0.2.0
   git push origin v0.2.0
   ```
7. Approve the PyPI publish in the GitHub Actions UI when prompted.
8. Sync `develop` after release:
   ```bash
   git checkout develop
   git pull origin develop
   git merge main
   git push origin develop
   ```
9. Delete the release branch:
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
3. Update `CHANGELOG.md` — see [Changelog Lifecycle](#changelog-lifecycle) below.
4. Commit and push:
   ```bash
   git add CHANGELOG.md
   git commit -m "chore: prepare release 0.2.0"
   git push origin release/v0.2.0
   ```
   This triggers the staging pipeline automatically.
5. Open PR: `release/v0.2.0` → `main`.
6. After staging passes and review is complete, merge to `main`.
7. Tag and push (triggers production release pipeline):
   ```bash
   git checkout main
   git pull origin main
   git tag v0.2.0
   git push origin v0.2.0
   ```
8. Approve PyPI publish, sync develop, and delete release branch (same as above).

### Release PR contract
- Source: `release/vx.y.z`
- Target: `main`
- Do NOT introduce new feature work in a release branch — only changelog updates.

## Versioning Rules

Follow [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`

| Type | When to use |
|---|---|
| `PATCH` | Bug fixes and minor backward-compatible improvements |
| `MINOR` | New backward-compatible features |
| `MAJOR` | Breaking changes to the CLI contract or output schema |

Version numbers follow a consistent pattern across artifacts. The git tag and GitHub Release use a `v` prefix. The release branch also uses the `v` prefix. Versioning is fully automatic via **setuptools-scm** — there is no `version` field in `pyproject.toml`.

| Artifact | Format | Example |
|---|---|---|
| Release branch | `release/vx.y.z` | `release/v2.4.2` |
| `pyproject.toml` | `dynamic = ["version"]` | Version derived from git tags via setuptools-scm |
| Git tag / GitHub Release | `vx.y.z` | `v2.4.2` |
| Changelog heading | `## [x.y.z] - YYYY-MM-DD` | `## [2.4.2] - 2026-03-22` |

**Never add `version = "..."` to `pyproject.toml`** — this will conflict with setuptools-scm.

### Version on `develop`
- The version on `develop` is derived automatically by setuptools-scm (e.g., `0.1.3.dev12`).
- Do NOT preemptively bump any version on `develop` for an upcoming release.
- Feature branches should not modify `pyproject.toml` version.

## Changelog Lifecycle

The changelog follows a two-phase lifecycle: development on `develop`, finalization on `release/vx.y.z`.

### Development phase (`develop`)
- Always maintain an `[Unreleased]` section at the top.
- Add all user-visible changes under `[Unreleased]`.
- Do NOT assign a version number on `develop`.
- Do NOT create future version sections on `develop`.

```markdown
## [Unreleased]

### Added
- New orchestration strategy for multi-turn evaluations.

### Fixed
- Corrected resource cleanup order in Foundry backend shutdown.
```

### Release phase (`release/vx.y.z`)
When creating the release branch, convert `[Unreleased]` into the versioned release entry, then add a fresh empty `[Unreleased]` section above it.

**Before:**
```markdown
## [Unreleased]

### Added
- New orchestration strategy...
```

**After:**
```markdown
## [Unreleased]

## [2.4.2] - 2026-03-22

### Added
- New orchestration strategy...
```

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
- Explain what changed and why it matters — include relevant technical context.
- Avoid vague wording: no "minor updates", "improvements", or "fixes" as standalone entries.

### Safety rules
- Never remove the `[Unreleased]` section.
- Never create more than one `[Unreleased]` section.
- Never assign a release version on `develop`.
- Never leave a release branch without converting `[Unreleased]` to the versioned entry.
- Never mismatch version numbers across branch name, changelog, and tag.

## Commit Guidelines

Use conventional commit format:

```
feat: add conversation metadata support
fix: correct chat history persistence issue
docs: update changelog for 2.4.2
chore: prepare release 2.4.2
```

## Required Secrets

Set in GitHub repo Settings → Secrets and variables → Actions:

| Secret | Purpose |
|---|---|
| `PIPY_TOKEN` | PyPI API token scoped to `agentops-toolkit` — used on merge to `main` |
| `TESTPYPI_API_TOKEN` | TestPyPI API token — used on tag push for pre-release validation |

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
- Never publish without running `python -m pytest tests/ -x -q` first.
