# Release Process

How we branch, version, and publish `agentops-toolkit`.

---

## Branches

| Branch | Purpose |
|---|---|
| `main` | Stable, published versions only |
| `develop` | Integration branch — all feature work lands here |
| `feature/<name>` | Created from `develop` for new work |
| `release/x.y.z` | Created from `develop` to prepare a release |

---

## Flow

```
feature/<name>
    │
    └──► PR to develop ──► merge
                               │
                           develop (stable, CI green)
                               │
                           git checkout -b release/x.y.z
                               │
                               ├─ bump version in pyproject.toml  ← first commit
                               ├─ finalize CHANGELOG.md
                               └─ git tag vx.y.z ──► push tag
                                                          │
                                                     [release.yml]
                                                          │
                                                     TestPyPI publish
                                                          │
                                               verify on TestPyPI
                                                          │
                               PR: release/x.y.z ──► main ──► merge
                                                                  │
                                                          [release.yml]
                                                                  │
                                                    PyPI publish + GitHub Release
                                                                  │
                                                    sync main ──► develop
```

---

## Version Conventions

| Artifact | Format | Example |
|---|---|---|
| Release branch | `release/x.y.z` | `release/0.1.2` |
| `pyproject.toml` | `x.y.z` | `0.1.2` |
| Git tag / GitHub Release | `vx.y.z` | `v0.1.2` |
| Changelog heading | `## [x.y.z] - YYYY-MM-DD` | `## [0.1.2] - 2026-03-23` |

Only the git tag and GitHub Release use the `v` prefix. PyPI displays the package as `agentops-toolkit 0.1.2`.

**When to bump the version:** as the very first commit on the `release/x.y.z` branch, before tagging or opening any PR. Never on `develop`.

---

## Changelog

- On `develop`: all changes go under `## [Unreleased]`.
- On `release/x.y.z`: convert `[Unreleased]` to `## [x.y.z] - YYYY-MM-DD` and add a fresh empty `[Unreleased]` above it.

---

## Workflows

- **`ci.yml`** — runs lint + tests on every push to `develop` and `release/**`, and on PRs targeting `develop` or `main`.
- **`release.yml`** — triggered by a `v*` tag push: builds the package and publishes to TestPyPI; then on merge to `main`, publishes to PyPI and creates the GitHub Release.

> **Note:** The TestPyPI/PyPI split in `release.yml` is planned. Currently the tag push publishes directly to PyPI.

---

## Required Secrets

| Secret | Purpose |
|---|---|
| `PIPY_TOKEN` | PyPI API token scoped to `agentops-toolkit` |
| `TESTPYPI_API_TOKEN` | TestPyPI API token for pre-release validation |

Set both in GitHub repo → Settings → Secrets and variables → Actions.
