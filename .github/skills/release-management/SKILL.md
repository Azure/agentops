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

---

## Branching Model

| Branch | Purpose |
|---|---|
| `main` | Always stable and deployment-ready. Only receives merges from `release/x.y.z` branches. |
| `develop` | Integration branch. All feature PRs target here. |
| `release/x.y.z` | Created by maintainers from `develop` when a release is ready to ship. |
| `feature/<name>` | Created by contributors from `develop` for all new work. |

**Default rule:** unless explicitly told otherwise, all work starts from `develop`.

---

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

---

## Release Workflow (Maintainers)

### Release branch naming
```
release/x.y.z
```
Examples: `release/2.4.2`, `release/0.2.0`

### Flow
1. Confirm `develop` is green (CI passes) and all intended changes are merged.
2. Create release branch from `develop`:
   ```bash
   git checkout develop
   git pull upstream develop
   git checkout -b release/x.y.z
   ```
3. **Immediately** update version in `pyproject.toml` — this is the first commit on the release branch, before tagging or opening any PR:
   ```toml
   [project]
   version = "x.y.z"
   ```
4. Update `CHANGELOG.md` — see [Changelog Lifecycle](#changelog-lifecycle) below.
5. Commit and push:
   ```bash
   git add pyproject.toml CHANGELOG.md
   git commit -m "chore: prepare release x.y.z"
   git push upstream release/x.y.z
   ```
6. Create and push the version tag (with `v` prefix) — this triggers publication to **TestPyPI**:
   ```bash
   git tag vx.y.z
   git push upstream vx.y.z
   ```
7. Verify the package on TestPyPI before proceeding:
   ```bash
   pip install --index-url https://test.pypi.org/simple/ agentops-toolkit==x.y.z
   agentops --help
   ```
8. Open PR: `release/x.y.z` → `main`. Include TestPyPI verification confirmation in the PR description.
9. After review and CI passes, merge into `main` — this triggers publication to **PyPI** and creates the GitHub Release.
10. Sync `develop` after release:
    ```bash
    git checkout develop
    git pull upstream main
    git push upstream develop
    ```

### Release PR contract
- Source: `release/x.y.z`
- Target: `main`
- Do NOT introduce new feature work in a release branch — only version bump and changelog.

---

## Versioning Rules

Follow [Semantic Versioning](https://semver.org/): `MAJOR.MINOR.PATCH`

| Type | When to use |
|---|---|
| `PATCH` | Bug fixes and minor backward-compatible improvements |
| `MINOR` | New backward-compatible features |
| `MAJOR` | Breaking changes to the CLI contract or output schema |

Version numbers follow a consistent pattern across artifacts. Only the git tag (and the GitHub Release it creates) uses a `v` prefix — everything else is bare semver:

| Artifact | Format | Example |
|---|---|---|
| Release branch | `release/x.y.z` | `release/2.4.2` |
| `pyproject.toml` | `version = "x.y.z"` | `version = "2.4.2"` |
| Git tag / GitHub Release | `vx.y.z` | `v2.4.2` |
| Changelog heading | `## [x.y.z] - YYYY-MM-DD` | `## [2.4.2] - 2026-03-22` |

This matches how PyPI displays the package (e.g., `agentops-toolkit 2.4.2`) while GitHub Releases conventionally show the `v` prefix (e.g., `v2.4.2`).

### Version on `develop`
- The version in `pyproject.toml` on `develop` reflects the latest shipped or in-progress state.
- Do NOT preemptively bump the version on `develop` for an upcoming release.
- Feature branches should not modify `pyproject.toml` version unless explicitly instructed.

---

## Changelog Lifecycle

The changelog follows a two-phase lifecycle: development on `develop`, finalization on `release/x.y.z`.

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

### Release phase (`release/x.y.z`)
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

All four release artifacts must be in sync:

| Artifact | Value |
|---|---|
| Release branch | `release/2.4.2` |
| `pyproject.toml` version | `2.4.2` |
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
- Never mismatch version numbers across branch name, `pyproject.toml`, changelog, and tag.

---

## Commit Guidelines

Use conventional commit format:

```
feat: add conversation metadata support
fix: correct chat history persistence issue
docs: update changelog for 2.4.2
chore: prepare release 2.4.2
```

---

## Required Secrets

Set in GitHub repo Settings → Secrets and variables → Actions:

| Secret | Purpose |
|---|---|
| `PIPY_TOKEN` | PyPI API token scoped to `agentops-toolkit` — used on merge to `main` |
| `TESTPYPI_API_TOKEN` | TestPyPI API token — used on tag push for pre-release validation |

---

## Default Decision Logic

| Situation | Action |
|---|---|
| Feature or code change | Base on `develop`, create `feature/*`, PR to `develop` |
| Release preparation | Base on `develop`, create `release/x.y.z`, update `pyproject.toml` + `CHANGELOG.md`, PR to `main` |
| Ambiguous instructions | Default to feature workflow on `develop`; do not assume a release unless explicitly requested |

---

## Guardrails
- Never create feature branches from `main`.
- Never open feature PRs to `main`.
- Never mix new feature work into a release branch.
- Never assign a release version on `develop`.
- Never tag without a green CI run.
- Never publish without running `python -m pytest tests/ -x -q` first.
