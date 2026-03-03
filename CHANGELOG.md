# Changelog

All notable changes to this project will be documented in this file.
This format follows [Keep a Changelog](https://keepachangelog.com/) and adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-__-__

### Added
- CLI command surface with Typer stubs:
  - `agentops init`
  - `agentops eval run --config <run.yaml> [--output <dir>]`
  - `agentops report --in <results.json> [--out <report.md>]`
- Unit tests for models, YAML/config loading, and workspace initialization behavior.
- Initial documentation including generic quickstart and test running guide.