# Changelog

All notable changes to this project will be documented in this file.

This project follows [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Initial Python `src` layout under `src/agentops`.
- CLI command surface with Typer stubs:
  - `agentops init`
  - `agentops eval run --config <run.yaml> [--output <dir>]`
  - `agentops report --in <results.json> [--out <report.md>]`
- Module entrypoint support via `python -m agentops`.
- Logging setup helper with default INFO and `--verbose` for DEBUG.
- Pydantic v2 models for configs and result schemas.
- YAML load/save utilities using `ruamel.yaml`.
- Config loader functions for workspace, bundle, dataset, and run configs.
- `agentops init` implementation with idempotent behavior and `--force` overwrite support.
- Unit tests for models, YAML/config loading, and workspace initialization behavior.
- Initial documentation including generic quickstart and test running guide.