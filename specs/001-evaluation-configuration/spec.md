# Feature Specification: Evaluation Configuration

**Feature Branch**: `placerda-spec-kit-feature`

**Created**: 2026-08-12

**Status**: Implemented Baseline

**Input**: This specification was reverse-engineered from the current implementation, tests, and public documentation of AgentOps Toolkit. It documents the existing `agentops.yaml` configuration surface as-built, not a new proposal. Sources reviewed include `src/agentops/core/agentops_config.py`, `src/agentops/core/config_loader.py`, `src/agentops/core/evaluators.py`, `tests/unit/test_agentops_config.py`, `tests/unit/test_evaluators.py`, and `docs/concepts.md`.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Describe an agent and dataset in one flat file (Priority: P1)

A release engineer wants to declare which agent to evaluate and which dataset to use for a Foundry prompt agent, a Foundry hosted endpoint, a generic HTTP/JSON agent, or a raw model deployment, using a single `agentops.yaml` file at the project root.

**Why this priority**: Without a valid, loadable configuration there is nothing to evaluate. This is the entry point for every other AgentOps capability.

**Independent Test**: Can be fully tested by writing a minimal `agentops.yaml` (`version`, `agent`, `dataset`) for each of the four supported agent value shapes and confirming the loader accepts it and correctly classifies the target kind, without executing any evaluation.

**Acceptance Scenarios**:

1. **Given** an `agentops.yaml` with `agent: "my-agent:3"`, **When** the configuration is loaded, **Then** the system classifies the target as a Foundry prompt agent with name `my-agent` and version `3`.
2. **Given** an `agentops.yaml` with `agent: "https://<resource>.services.ai.azure.com/api/projects/<project>/agents/..."`, **When** the configuration is loaded, **Then** the system classifies the target as a Foundry hosted agent reachable over REST.
3. **Given** an `agentops.yaml` with `agent: "https://api.example.com/chat"` and no Foundry-specific path shape, **When** the configuration is loaded, **Then** the system classifies the target as a generic HTTP/JSON agent.
4. **Given** an `agentops.yaml` with `agent: "model:gpt-4o"`, **When** the configuration is loaded, **Then** the system classifies the target as a raw model deployment.
5. **Given** a `dataset` path naming a file that has not yet been inspected, **When** the configuration is loaded, **Then** loading succeeds on the strength of the YAML fields alone (`version`, `agent`, `dataset` present and well-formed); whether that file exists, parses as JSONL, and contains usable rows is determined afterward, during evaluation preparation, not during configuration loading.

---

### User Story 2 - Let the system infer evaluators and thresholds, or override them (Priority: P2)

A release engineer wants sensible evaluators and pass/fail thresholds chosen automatically based on the agent type and dataset columns, but wants the ability to override the evaluator list or specific threshold expressions when the defaults do not fit.

**Why this priority**: Automatic inference removes configuration burden for the common case; overrides are needed once teams have specific quality bars, so this is the second most critical capability after basic loading.

**Independent Test**: Can be fully tested by loading configurations that vary only in dataset columns present (e.g., with/without `context`, with/without `tool_calls`) and confirming the evaluator set changes accordingly, then adding an explicit `evaluators:` list and confirming inference is bypassed, and adding a `thresholds:` map and confirming a user-specified expression overrides the default for that metric while other metrics keep their defaults.

**Acceptance Scenarios**:

1. **Given** a dataset with `input` and `expected` columns only, **When** evaluators are resolved, **Then** the system selects the default quality evaluator set for that agent type without requiring an explicit `evaluators:` entry.
2. **Given** a dataset that additionally has a `context` column, **When** evaluators are resolved, **Then** retrieval/grounding-oriented evaluators are added to the selected set.
3. **Given** a dataset that additionally has `tool_definitions` and `tool_calls` columns, **When** evaluators are resolved, **Then** agent-workflow evaluators (tool call accuracy, task completion, and related) are added to the selected set.
4. **Given** an `agentops.yaml` with an explicit `evaluators:` list, **When** evaluators are resolved, **Then** the explicit list is used verbatim and automatic inference is skipped.
5. **Given** an `agentops.yaml` with `thresholds: {coherence: ">=4"}` and no other threshold entries, **When** thresholds are resolved, **Then** the `coherence` threshold uses the user value `>=4` while every other metric keeps its inferred default.

---

### User Story 3 - Configure protocol-specific and safety-relevant fields for URL-based agents (Priority: P3)

A release engineer integrating a generic HTTP/JSON or Foundry hosted agent wants to configure how requests are shaped and how responses are parsed (protocol, request/response field paths, headers, authentication, streaming aggregation), and wants invalid configuration caught before any network call is made.

**Why this priority**: This unlocks evaluation of the broadest class of custom agents (LangGraph, LangChain, ACA, AKS, custom REST) but is only needed once the basic agent/dataset declaration and evaluator inference already work, so it ranks below them.

**Independent Test**: Can be fully tested by constructing configurations with different `protocol`, `response_mode`, `request_field`, `response_field`, `tool_calls_field`, `headers`, `auth_header_env`, `auth_header_name`, `auth_value_template`, and `stream` values, and verifying the loader accepts well-formed combinations and rejects malformed ones (e.g., unsupported protocol name) with a clear, actionable error, without invoking the agent endpoint.

**Acceptance Scenarios**:

1. **Given** a URL-based agent configuration with `protocol: http-json`, `request_field: message`, and `response_field: text`, **When** the configuration is loaded, **Then** it validates successfully and the resolved settings are available for the execution layer to consume.
2. **Given** a URL-based agent configuration with `response_mode: sse` and a `stream` block specifying `text_field` and `done_marker`, **When** the configuration is loaded, **Then** the streaming aggregation settings are accepted and preserved.
3. **Given** a configuration with an unsupported `protocol` value, **When** the configuration is loaded, **Then** loading fails with a validation error naming the offending field, and no evaluation run is attempted.
4. **Given** a configuration with `auth_header_env` set to a variable name, **When** the configuration is loaded, **Then** the loader accepts the field as a reference to be resolved at execution time and does not require the variable to be already set in the environment at load time.

---

### Edge Cases

- What happens when `agentops.yaml` is missing required fields (`version`, `agent`, or `dataset`)? Loading MUST fail with a validation error identifying the missing field(s) rather than raising an unhandled exception.
- What happens when `version` is present but not the supported value `1`? Loading MUST fail with a clear, actionable error rather than silently coercing the schema.
- How does the system handle a `dataset` path that does not exist on disk? Configuration loading itself does not open or inspect the dataset file, so a missing file does not fail at load time; the missing-file condition is caught during the later evaluation-preparation step (dataset shape detection), which fails with a file-not-found style error referencing the configured path before any row is invoked.
- How does the system handle a JSONL dataset row that is missing the required `input` field? Neither configuration loading nor evaluation-preparation's dataset-shape detection reads every row for an `input` field; the missing field is caught when that specific row is invoked, which fails that row while the run continues with the remaining rows.
- What happens when both an explicit `evaluators:` override and dataset columns that would normally trigger additional evaluators are both present? The explicit override MUST win; inference MUST NOT silently add evaluators on top of the override.
- What happens when `execution: cloud` is combined with an `agent:` value that is not a Foundry agent (prompt or hosted)? Configuration loading does not reject this combination by itself; the restriction is enforced when the evaluation run starts, which fails with a clear error before the target or any evaluator is invoked, and before any Azure/Foundry call is made.
- What happens when both `project_endpoint` in `agentops.yaml` and the `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` environment variable are set to different values? The `agentops.yaml` value MUST take precedence.
- How does the system handle a `thresholds:` map containing a metric name that does not correspond to any selected evaluator? The unused threshold entry MUST NOT crash configuration loading; unmatched threshold keys are simply not applied to any evaluator.
- How does the system handle a dataset file that exists but contains zero usable rows? Evaluation preparation MUST reject it with a clear "dataset is empty" error before any row is invoked; a zero-row run is not a supported outcome.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST load a single flat `agentops.yaml` file at the project root as the sole source of evaluation configuration, with no separate bundle, scenario, or per-dataset configuration files required.
- **FR-002**: The system MUST require `version`, `agent`, and `dataset` fields, and MUST reject configurations missing any of these with a validation error identifying the missing field.
- **FR-003**: The system MUST classify the `agent` field into one of four target kinds based on its value shape: Foundry prompt agent (`name:version`), Foundry hosted agent (a Foundry-shaped `https://` URL), generic HTTP/JSON agent (any other `https://` URL), or raw model deployment (`model:<deployment>`).
- **FR-004**: The system MUST record the `dataset` field as a file path at configuration-load time without opening, parsing, or validating the referenced file's existence, JSONL structure, or per-row contents; those checks happen later, during evaluation preparation and per-row invocation, not during configuration loading.
- **FR-005**: The system MUST recognize optional dataset columns (`expected`, `context`, `tool_definitions`, `tool_calls`) and use their presence to drive automatic evaluator selection.
- **FR-006**: The system MUST automatically select an evaluator set based on the classified agent target kind and the dataset columns present, without requiring the user to enumerate evaluators.
- **FR-007**: The system MUST allow an explicit `evaluators:` list in `agentops.yaml` to fully override automatic evaluator selection for that run.
- **FR-008**: The system MUST provide default threshold expressions for every selected evaluator metric and MUST allow a user-supplied `thresholds:` map to override the default expression for any subset of metrics while leaving the rest at their defaults.
- **FR-009**: The system MUST support an `execution` field with values `local` (the default), `cloud`, `azd`, and `auto`. At configuration-load time, the system MUST reject `execution: azd` when the classified agent target is not a Foundry prompt or Foundry hosted agent. The system MUST also restrict `execution: cloud` to Foundry prompt or Foundry hosted agent targets with a derivable name and version, but this restriction is enforced when the evaluation run starts rather than at configuration-load time.
- **FR-010**: The system MUST support an `eval_recipe` field usable when `execution: azd`, and MUST be able to proceed without it by auto-discovering a single eval recipe when exactly one is present.
- **FR-011**: The system MUST support a `publish` boolean field that, combined with `execution: local`, controls whether results are additionally uploaded to the Classic Foundry Evaluations panel.
- **FR-012**: The system MUST support a `project_endpoint` field in `agentops.yaml` that takes precedence over the `AZURE_AI_FOUNDRY_PROJECT_ENDPOINT` environment variable when both are set.
- **FR-013**: The system MUST support URL-based agent configuration fields `protocol` (`responses`, `invocations`, `http-json`), `request_field`, `response_field`, `tool_calls_field`, `headers`, `auth_header_env`, `auth_header_name`, and `auth_value_template`, applying documented defaults when any is omitted.
- **FR-014**: The system MUST support a `response_mode` field (`json`, `sse`, `text`) and, when `response_mode` is `sse` or `text`, MUST accept a `stream` block describing streaming aggregation (`text_field`, `done_marker`, `strip_leading_token`).
- **FR-015**: The system MUST reject an unsupported `protocol` or `response_mode` value at configuration-load time with a validation error naming the offending field, before any network call is attempted.
- **FR-016**: The system MUST support optional evidence-reference fields `assert_path`, `acs_path`, and `redteam_path` that record paths to external policy/results artifacts without executing those external tools itself.
- **FR-017**: The system MUST treat configuration validation as a purely local, offline operation that does not require network access or Azure credentials to succeed or fail.

### Key Entities

- **AgentOpsConfig**: The root configuration object parsed from `agentops.yaml`, holding `version`, `agent`, `dataset`, `thresholds`, `evaluators`, `project_endpoint`, `execution`, `eval_recipe`, `publish`, protocol/streaming fields, and evidence-reference path fields.
- **TargetResolution**: The classified interpretation of the `agent` field, capturing the resolved kind (Foundry prompt agent, Foundry hosted agent, generic HTTP/JSON agent, or model deployment) plus any parsed name/version/url/deployment components.
- **Dataset Row**: A single JSONL record consumed at invocation time; it must carry an `input` field to be invoked successfully, and may carry optional `expected`, `context`, `tool_definitions`, and `tool_calls` fields that influence evaluator inference during evaluation preparation.
- **Evaluator Preset**: A named, cataloged evaluator definition (score key, input mapping, default threshold expression, applicable agent-kind/category) used both for automatic inference and for validating explicit `evaluators:` overrides.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of the four supported `agent` value shapes (Foundry prompt, Foundry hosted, generic HTTP/JSON, raw model deployment) resolve to the correct, single target kind from `version` + `agent` + `dataset` alone, with no additional configuration required.
- **SC-002**: Every documented configuration rejection condition (a missing required field, an unsupported `version`, an unsupported `protocol`, an unsupported `response_mode`, `execution: azd` on a non-Foundry target) is reported before any dataset row is read or any network call is made, and each rejection names the offending field.
- **SC-003**: For the same agent target, configuration fields, and dataset column shape, the automatically selected evaluator set and default thresholds are stable across repeated evaluation preparation; changing the agent kind or relevant dataset columns may change selection.
- **SC-004**: When an explicit `evaluators:` list is present, the resolved evaluator set is always exactly that list - never the list plus any evaluator that column-based inference would have added.
- **SC-005**: A user-supplied threshold for one metric changes the resolved threshold for that metric only; every other metric's resolved threshold is unchanged from its documented default.
- **SC-006**: `execution: cloud` is rejected for every target kind other than a Foundry prompt or Foundry hosted agent, and accepted for both of those kinds when a name and version can be derived; the rejection surfaces before the agent or any evaluator runs.

## Assumptions

- Teams have exactly one `agentops.yaml` per evaluated project/workspace; multi-agent or multi-dataset scenarios are handled by maintaining separate project directories rather than a single richer schema.
- The evaluator catalog and its default thresholds are maintained inside AgentOps and are considered part of this baseline; changes to individual evaluator defaults are out of scope for this specification.
- Users are expected to run `agentops init` or otherwise scaffold the workspace before hand-editing `agentops.yaml`, so this specification assumes a syntactically valid YAML file is the starting point for validation.
- Configuration loading validates only the YAML/schema fields themselves; dataset existence, JSONL well-formedness, row count, and per-row field presence are validated later, during evaluation preparation and per-row invocation (see the Evaluation Execution specification), not during configuration loading.
- Network/Azure credential validity for `project_endpoint`, `auth_header_env`, and similar fields is checked at execution time (see the Evaluation Execution specification), not at configuration-load time.

## Out of Scope

- Actually invoking the agent or running evaluators against it (covered by the Evaluation Execution specification).
- Interpreting or executing `assert_path`, `acs_path`, or `redteam_path` contents; this specification only covers accepting and recording these paths.
- Adding new evaluator types or changing default threshold values for existing evaluators.
- Multi-file or hierarchical configuration composition; the schema is intentionally a single flat file.
- CI/CD workflow generation, release evidence, and Doctor readiness checks, which consume this configuration but are specified separately.

## Implementation Evidence

- `src/agentops/core/agentops_config.py` - Pydantic model for the flat `agentops.yaml` v1 schema, including `version`, `agent`, `dataset`, `thresholds`, `evaluators`, `project_endpoint`, `execution`, `eval_recipe`, `publish`, `protocol`, `request_field`, `response_field`, `tool_calls_field`, `headers`, `auth_header_env`, `auth_header_name`, `auth_value_template`, `response_mode`, `stream`, `assert_path`, `acs_path`, `redteam_path`, and the agent-target classification logic.
- `src/agentops/core/config_loader.py` - loads and validates `agentops.yaml` into the config model, raising validation errors for malformed configuration.
- `src/agentops/core/evaluators.py` - dataset shape detection, evaluator catalog, automatic evaluator selection, and threshold default/override merging.
- `tests/unit/test_agentops_config.py` - unit tests for schema parsing, agent-kind classification, and field validation.
- `tests/unit/test_evaluators.py` - unit tests for evaluator inference by agent kind and dataset shape, explicit `evaluators:` override behavior, and threshold merging.
- `tests/unit/test_agentops_config_identity.py` - additional configuration/identity-adjacent field validation coverage.
- `docs/concepts.md` and `docs/how-it-works.md` - narrative documentation of the configuration model and evaluation scenario matrix consistent with this baseline.
