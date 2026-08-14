# Feature Specification: CI/CD Workflow Generation

**Feature Branch**: `placerda-spec-kit-feature`

**Created**: 2026-08-12

**Status**: Implemented Baseline

**Input**: This specification was reverse-engineered from the current implementation, tests, and public documentation of AgentOps Toolkit. It documents the existing `agentops workflow analyze`/`agentops workflow generate` behavior as-built, not a new proposal. Sources reviewed include `src/agentops/services/workflow_analysis.py`, `src/agentops/services/cicd.py`, the packaged templates under `src/agentops/templates/workflows/` and `src/agentops/templates/pipelines/azuredevops/`, and `tests/unit/test_cicd.py` and `test_workflow_analysis.py`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Understand what CI/CD setup is recommended before generating anything (Priority: P1)

A release engineer runs `agentops workflow analyze` to see, without writing any files, which deploy mode and evaluation runner AgentOps recommends for the current repository, and which workflow files already exist versus would need to be created.

**Why this priority**: Teams need a safe, read-only way to preview what generation would do before committing to writing files into their repository; this is the natural first step in adopting CI/CD generation.

**Independent Test**: Can be fully tested by running `agentops workflow analyze` against a repository in various states (no workflows present, some workflows present, azd project detected, no azd project detected) and confirming the reported recommended deploy mode and file-existence findings match the repository's actual state, with zero files written.

**Acceptance Scenarios**:

1. **Given** a repository with no existing AgentOps workflow files, **When** `agentops workflow analyze` is run, **Then** the output reports which workflow files would be created, the recommended deploy mode, and the recommended evaluation runner, without writing any file.
2. **Given** a repository that already contains AgentOps-managed workflow files, **When** `agentops workflow analyze` is run, **Then** the output distinguishes files that already exist from files that are missing.
3. **Given** a repository recognized as an Azure Developer CLI (azd) project, **When** `agentops workflow analyze` is run, **Then** the recommended deploy mode reflects azd-aware provisioning rather than the generic placeholder mode.

---

### User Story 2 - Generate PR and environment-promotion workflows for GitHub Actions or Azure DevOps (Priority: P1)

A release engineer runs `agentops workflow generate` to create a consistent set of workflow/pipeline files covering pull-request evaluation gating and dev/qa/prod deployment promotion, for either GitHub Actions or Azure DevOps, without overwriting files the team has already customized.

**Why this priority**: Generating the actual CI/CD templates is the core value of this feature; it turns the recommendation from User Story 1 into concrete, runnable automation.

**Independent Test**: Can be fully tested by running `agentops workflow generate --platform github` and `--platform azure-devops` against a clean directory and confirming the expected set of files is written under `.github/workflows/` or the Azure DevOps pipelines location respectively, then re-running generation without `--force` and confirming existing files are left untouched and reported as skipped.

**Acceptance Scenarios**:

1. **Given** a clean repository and `--platform github` (the default), **When** `agentops workflow generate` is run with the default kinds, **Then** GitHub Actions workflow files for pull-request gating and dev/qa/prod deployment are written under `.github/workflows/`.
2. **Given** the same command with `--platform azure-devops`, **When** run, **Then** equivalent Azure DevOps pipeline YAML files are written to the Azure DevOps pipelines location instead.
3. **Given** a repository where a previously generated workflow file has since been manually edited, **When** `agentops workflow generate` is run again without `--force`, **Then** that file is left unmodified and reported as skipped rather than overwritten.
4. **Given** the same scenario, **When** `agentops workflow generate --force` is run, **Then** the previously edited file is overwritten with the current template content.
5. **Given** `--kinds pr,dev` is passed explicitly, **When** generation runs, **Then** only the pull-request and dev-environment templates are generated; qa, prod, and doctor templates are not written.

---

### User Story 3 - Choose a deploy mode and a Doctor readiness gate for generated workflows (Priority: P2)

A release engineer wants generated deployment workflows to match their infrastructure approach (a stack-agnostic placeholder, an azd-driven provision/deploy flow, or a Foundry prompt-agent candidate/eval/deploy flow), and wants the pull-request workflow to enforce a configurable Doctor readiness severity gate before merge.

**Why this priority**: This tailors the generated automation to a team's actual deployment stack and desired readiness bar; it depends on the base generation capability in User Story 2 already working.

**Independent Test**: Can be fully tested by running generation with each of the four `--deploy-mode` values and each of the supported `--doctor-gate` severities, and confirming the generated PR/deploy templates reference the corresponding deploy approach and gate severity.

**Acceptance Scenarios**:

1. **Given** `--deploy-mode auto` and a repository recognized as an azd project, **When** generation runs, **Then** the azd-specific deploy templates are selected automatically.
2. **Given** `--deploy-mode placeholder` is passed explicitly, **When** generation runs, **Then** the stack-agnostic placeholder deploy templates are written regardless of what `auto` would have selected.
3. **Given** `--deploy-mode prompt-agent`, **When** generation runs, **Then** Foundry prompt-agent candidate/eval/deploy templates are written instead of the generic deploy templates.
4. **Given** `--doctor-gate warning` is passed, **When** the pull-request workflow template is generated, **Then** the generated template's Doctor gate severity threshold reflects `warning` rather than the default `critical`.
5. **Given** an unsupported value is passed to `--deploy-mode` or `--doctor-gate`, **When** generation is invoked, **Then** the command fails with a validation error listing the valid values, and no files are written.

---

### Edge Cases

- What happens when `--kinds` includes an unrecognized kind name? Unknown kinds are ignored rather than causing the whole generation command to fail.
- What happens when the `doctor` workflow kind is requested? It is generated from a scheduled readiness-check template (historically named "watchdog" in the source template path) and written out under a Doctor-named output file; the legacy kind name `watchdog` is still accepted as an alias for `doctor`.
- How does the system handle generating for `--platform azure-devops` when the target directory has no existing Azure DevOps pipelines folder? The folder structure is created as part of writing the generated files.
- What happens when both an azd-specific template and a non-azd template exist for the same kind and `--deploy-mode azd` is selected? The azd-specific template is used for that kind instead of the generic one.
- What happens when `agentops workflow generate` is run twice in a row with identical flags and no manual edits in between? The second run reports every pre-existing output file as skipped because `--force` was not supplied; it does not need to compare file contents.
- What happens when only some of the requested `--kinds` have azd-specific templates available under `--deploy-mode azd`? Kinds without an azd-specific template fall back to their standard template rather than failing the whole command.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST provide a read-only `agentops workflow analyze` command that reports the recommended deploy mode, the recommended evaluation runner, and which workflow files currently exist versus would be created, without writing any file.
- **FR-002**: The system MUST provide an `agentops workflow generate` command that writes workflow/pipeline files for one or more of the kinds `pr`, `dev`, `qa`, `prod`, and `doctor`.
- **FR-003**: The system MUST support `--platform github` (default) writing GitHub Actions workflow YAML under `.github/workflows/`, and `--platform azure-devops` writing equivalent Azure DevOps pipeline YAML to the Azure DevOps pipelines location.
- **FR-004**: The system MUST support `--deploy-mode` values `auto`, `placeholder`, `azd`, and `prompt-agent`, where `auto` resolves to a concrete mode based on repository detection (for example, recognizing an azd project) and the other three are explicit selections.
- **FR-005**: The system MUST support `--doctor-gate` values that set the Doctor readiness severity floor enforced by the generated pull-request workflow before allowing a merge-gating step to pass.
- **FR-006**: The system MUST support `--kinds` as a comma-separated subset of the five supported kinds, generating only the requested kinds and silently ignoring unrecognized kind names.
- **FR-007**: The system MUST NOT overwrite an existing generated file unless `--force` is passed, and MUST report untouched pre-existing files as skipped.
- **FR-008**: The system MUST overwrite existing generated files with current template content when `--force` is passed.
- **FR-009**: The system MUST accept the legacy kind name `watchdog` as an alias for the `doctor` kind for backward compatibility.
- **FR-010**: The system MUST reject an unsupported `--deploy-mode` or `--doctor-gate` value with a validation error that lists the valid values, without writing any file.
- **FR-011**: The system MUST select azd-specific templates for a given kind when `--deploy-mode azd` (or `auto` resolving to azd) is active and an azd-specific template exists for that kind, falling back to the standard template for kinds without an azd-specific variant.
- **FR-012**: The system MUST only generate static template files (workflow/pipeline YAML); it MUST NOT execute, trigger, or validate any CI/CD run as part of generation.

### Key Entities

- **CicdResult**: The outcome of a generation invocation, capturing the effective deploy mode, the effective evaluation runner, and the lists of written versus skipped files.
- **Workflow Kind**: One of `pr`, `dev`, `qa`, `prod`, or `doctor`, each mapping to one or more source template files and output file locations per platform.
- **Deploy Mode**: One of `auto`, `placeholder`, `azd`, or `prompt-agent`, determining which template variant is used for deployment-oriented kinds.
- **Doctor Gate**: The configured Doctor severity floor (for example, `critical` or `warning`) embedded into the generated pull-request workflow's readiness-check step.
- **Platform Template Map**: The per-platform (GitHub Actions vs Azure DevOps) mapping from kind and deploy mode to a source template path and output path.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: `agentops workflow analyze` never writes, modifies, or deletes any file in the target repository, regardless of the repository's state.
- **SC-002**: For every one of the five workflow kinds on every one of the two platforms, running generation produces the corresponding output file at its documented location - no requested kind/platform combination is silently skipped.
- **SC-003**: Re-running `agentops workflow generate` without `--force` after a manual edit never alters the manually edited file's content; the file is reported as skipped, not overwritten.
- **SC-004**: Each explicit `--deploy-mode` selects its documented template strategy, `auto` resolves to one supported concrete strategy from repository signals, and each supported `--doctor-gate` value is reflected in the generated pull-request gate where applicable.
- **SC-005**: Passing an unsupported `--deploy-mode` or `--doctor-gate` value always results in a non-zero exit and zero files written to the target repository.
- **SC-006**: Every recognized requested workflow kind is either written to disk or explicitly reported as skipped because its output file already exists; unrecognized kind names are silently ignored as documented, while invalid deploy-mode or Doctor-gate values reject the command before files are written.

## Assumptions

- Generated workflow/pipeline files are intended to be committed by the user after review; this feature does not commit, push, or open pull requests on the user's behalf.
- The `doctor` workflow kind's underlying template historically used the file name "watchdog"; this baseline treats `doctor` as the current, supported kind name and `watchdog` as a backward-compatible alias, not a separate capability.
- Azure DevOps generation assumes the user will place the generated pipeline YAML into their organization's pipeline definitions; this feature does not call the Azure DevOps REST API to register a pipeline.
- Deploy-mode `auto` detection (for example, recognizing an azd project) relies on local file-system signals in the target repository, not on any network call.

## Out of Scope

- Executing, triggering, or validating a CI/CD run; this feature only produces static template files.
- Creating or managing Azure DevOps pipeline definitions via the Azure DevOps REST API or `az pipelines` CLI.
- Provisioning or deploying actual Azure infrastructure; azd-mode templates reference `azd` commands but this feature does not invoke them.
- Doctor's own readiness analysis logic, which is specified separately; this feature only wires a configured severity gate into generated templates.
- Release evidence composition, which is specified separately and may be invoked from within a generated deployment workflow but is not generated by this feature.

## Implementation Evidence

- `src/agentops/services/cicd.py` - `generate_cicd_workflows()`, `DEPLOY_MODES`, `ALL_KINDS`, `LEGACY_KIND_ALIASES` (`{"watchdog": "doctor"}`), `DOCTOR_GATES`, per-platform template maps, and the force/skip file-write logic.
- `src/agentops/services/workflow_analysis.py` - read-only `agentops workflow analyze` logic, including `recommended_deploy_mode()` and `recommended_eval_runner()` detection helpers.
- `src/agentops/templates/workflows/` - GitHub Actions templates including `agentops-pr.yml`, `agentops-pr-prompt-agent.yml`, `agentops-deploy-dev.yml`, `agentops-deploy-dev-azd.yml`, `agentops-deploy-qa.yml`, `agentops-deploy-qa-azd.yml`, `agentops-deploy-prod.yml`, `agentops-deploy-prod-azd.yml`, `agentops-deploy-prompt-agent.yml`, and `agentops-watchdog.yml` (source template for the `doctor` kind).
- `src/agentops/templates/pipelines/azuredevops/` - the equivalent Azure DevOps pipeline YAML set mirroring the GitHub Actions templates above.
- `src/agentops/cli/app.py` - `workflow analyze` and `workflow generate` Typer commands wiring `--platform`, `--deploy-mode`, `--doctor-gate`, `--kinds`, and `--force`.
- `tests/unit/test_cicd.py` - unit coverage of template generation, force/skip behavior, deploy-mode selection, and doctor-gate substitution.
- `tests/unit/test_workflow_analysis.py` - unit coverage of the read-only analyze command's recommendations and file-existence reporting.
- `docs/ci-github-actions.md` - narrative documentation of the generated GitHub Actions workflow set consistent with this baseline.
