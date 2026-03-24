# Release Process

How we branch, version, and publish `agentops-toolkit`.

---

## Branches

| Branch | Purpose |
|---|---|
| `main` | Stable, published versions only |
| `develop` | Integration branch — all feature work lands here |
| `feature/<name>` | Created from `develop` for new work |
| `release/vx.y.z` | Created from `develop` to prepare a release |

---

## Flow

```
feature/<name>
    │
    └──► PR to develop ──► merge
                               │
                           develop (stable, CI green)
                               │
                           cut-release.yml (or manual branch)
                               │
                           git checkout -b release/vx.y.z
                               │
                               ├─ finalize CHANGELOG.md          ← automated by cut-release
                               └─ push branch
                                       │
                                  [staging.yml]
                                       │
                                  build ──► TestPyPI ──► verify
                                       │
                               PR: release/vx.y.z ──► main ──► merge
                                       │
                                  git tag vx.y.z ──► push tag
                                       │
                                  [release.yml]
                                       │
                                  build ──► TestPyPI ──► verify
                                       │
                                  ⏸ approval gate (release environment)
                                       │
                                  PyPI publish ──► GitHub Release
                                       │
                                  sync main ──► develop
```

---

## Version Conventions

| Artifact | Format | Example |
|---|---|---|
| Release branch | `release/vx.y.z` | `release/v0.1.2` |
| Git tag / GitHub Release | `vx.y.z` | `v0.1.2` |
| Changelog heading | `## [x.y.z] - YYYY-MM-DD` | `## [0.1.2] - 2026-03-23` |
| PyPI package | `x.y.z` | `agentops-toolkit 0.1.2` |
| Dev builds (TestPyPI) | `x.y.z.devN` | `0.1.3.dev5` |

Version is **never set manually** — [setuptools-scm](https://github.com/pypa/setuptools-scm) derives it from git tags at build time. There is no `version` field in `pyproject.toml`.

---

## Changelog

- On `develop`: all changes go under `## [Unreleased]`.
- On `release/vx.y.z`: convert `[Unreleased]` to `## [x.y.z] - YYYY-MM-DD` and add a fresh empty `[Unreleased]` above it.
- The `cut-release.yml` workflow automates this conversion when creating the release branch.

---

## Workflows

| Workflow | Trigger | Purpose |
|---|---|---|
| `ci.yml` | Push to `develop`, PRs to `main`/`develop` | Lint + tests; publishes dev builds to TestPyPI on `develop` pushes |
| `staging.yml` | Push to `release/**` | Build → TestPyPI → verify install (staging validation) |
| `release.yml` | Push of `v*` tag | Build → TestPyPI → verify → **approval gate** → PyPI → GitHub Release |
| `cut-release.yml` | Manual dispatch | Creates `release/vx.y.z` branch from `develop`, updates CHANGELOG, opens PR to `main` |
| `_build.yml` | Called by staging and release | Reusable workflow: runs tests and builds the package |

---

## Required Secrets & Environments

| Secret | Environment | Purpose |
|---|---|---|
| `TEST_PYPI_TOKEN` | `staging` | TestPyPI API token for pre-release validation |
| `PYPI_TOKEN` | `release` | PyPI API token scoped to `agentops-toolkit` |

Set secrets in GitHub repo → Settings → Secrets and variables → Actions (scoped to environments).

The `release` environment requires **manual approval** before publishing to PyPI.
