# Feature Specification: Evaluation Execution

**Feature Branch**: `placerda-spec-kit-feature`

**Created**: 2026-08-12

**Status**: Implemented Baseline

**Input**: This specification was reverse-engineered from the current implementation, tests, and public documentation of AgentOps Toolkit. It documents the existing evaluation-run execution behavior as-built, not a new proposal. Sources reviewed include `src/agentops/pipeline/orchestrator.py`, `runtime.py`, `invocations.py`, `cloud_runner.py`, `cloud_results.py`, `publisher.py`, `azd_runner.py`, and their associated unit/integration tests.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run an evaluation locally against any agent type (Priority: P1)

A release engineer runs `agentops eval run` against a Foundry hosted agent, a generic HTTP/JSON agent, or a raw model deployment, and gets a normalized run of the agent invoked once per dataset row with local evaluators scoring each response.

**Why this priority**: Local execution is the default and most universally applicable execution path; it works for every agent kind and requires no cloud evaluation service, so it is the foundational capability the rest of execution builds on.

**Independent Test**: Can be fully tested end-to-end by pointing `agentops eval run` at a small local HTTP echo agent and a JSONL dataset, then confirming each row is invoked exactly once, evaluators run against the response, and a normalized result set is produced without any cloud evaluation dependency.

**Acceptance Scenarios**:

1. **Given** a valid `agentops.yaml` with `execution: local` (or the field omitted, since local is the default) and a reachable HTTP/JSON agent, **When** `agentops eval run` is executed, **Then** the agent is invoked once per dataset row and each response is scored by the evaluators selected for that configuration.
2. **Given** the same setup but the agent target is a raw model deployment (`model:<deployment>`), **When** `agentops eval run` is executed, **Then** the model deployment is called directly per row and evaluated the same way as any other local target.
3. **Given** a URL-based agent configured with `response_mode: sse` and a `stream` aggregation block, **When** `agentops eval run` is executed, **Then** the streamed response is reassembled into a single response text before being scored.
4. **Given** an agent invocation that fails for one dataset row (for example, a network error), **When** the run completes, **Then** the failure is captured and attributed to that row rather than silently aborting the entire run or crashing without diagnostic information.

---

### User Story 2 - Run a Foundry prompt agent through the cloud evaluation service (Priority: P2)

A release engineer with a Foundry prompt agent (`name:version`) wants the dataset submitted to the OpenAI Evals API through Foundry so the agent and evaluators run server-side, with results normalized back into the same result shape as a local run.

**Why this priority**: Cloud evaluation offloads compute and centralizes evaluation history in Foundry, which matters once teams adopt Foundry prompt agents, but it is a narrower, agent-kind-specific path layered on top of the local execution foundation.

**Independent Test**: Can be fully tested by configuring `execution: cloud` with a Foundry prompt agent target, running `agentops eval run`, and confirming the dataset is synced, a cloud run/eval identifier is returned, and the resulting normalized output items match the same `RunResult` shape produced by local execution.

**Acceptance Scenarios**:

1. **Given** an `agentops.yaml` with `execution: cloud` and `agent: "my-agent:3"`, **When** `agentops eval run` is executed, **Then** the dataset is synced to the cloud evaluation service and a cloud evaluation/run identifier is captured.
2. **Given** a completed cloud run, **When** results are retrieved, **Then** cloud output items are normalized into the same row/metric structure used by local runs, so downstream reporting does not need to distinguish execution mode.
3. **Given** `execution: cloud` is combined with an agent target that is not a Foundry prompt or Foundry hosted agent, **When** `agentops eval run` is executed, **Then** the run is rejected before any cloud submission occurs.

---

### User Story 3 - Delegate execution to azd and normalize its output (Priority: P3)

A team already using Azure Developer CLI (`azd`) evaluation recipes wants `agentops eval run` to delegate execution to `azd ai agent eval` and fold the emitted metrics into the standard AgentOps result contract.

**Why this priority**: This path serves teams with an existing azd-centric workflow; it is valuable but narrower in audience than local or cloud execution, so it is prioritized last among the three execution modes.

**Independent Test**: Can be fully tested by configuring `execution: azd` with a discoverable `eval.yaml` recipe (or an explicit `eval_recipe` path), running `agentops eval run`, and confirming the azd command is invoked and its emitted metrics are normalized into the standard result schema.

**Acceptance Scenarios**:

1. **Given** `execution: azd` and exactly one discoverable `eval.yaml` recipe in the workspace, **When** `agentops eval run` is executed, **Then** the recipe is auto-discovered without requiring an explicit `eval_recipe` path.
2. **Given** `execution: azd` with an explicit `eval_recipe` path, **When** `agentops eval run` is executed, **Then** that recipe is used instead of auto-discovery.
3. **Given** an azd delegated run completes, **When** results are retrieved, **Then** the emitted azd metrics are normalized into the same result schema produced by local and cloud execution.

---

### Edge Cases

- What happens when the configured agent endpoint is unreachable for the entire run (not just one row)? The run MUST surface a runtime error distinct from a threshold failure, rather than reporting a false pass or false threshold-fail.
- How does the system handle a dataset with zero rows? The run MUST reject it before any row is invoked, with a clear "dataset is empty" error; a zero-row run is not treated as a completed pass.
- What happens when `publish: true` is set but the local run's authentication to Foundry is invalid? Publishing MUST fail without discarding or corrupting the already-computed local `results.json`/`report.md`.
- What happens when `execution: cloud` is used and the cloud service returns a subset of the submitted rows (e.g., partial completion)? The normalization step MUST reflect the actual number of returned rows rather than assuming full completion.
- How does the system behave when both `--baseline` (see Evaluation Results and Regression) and `execution: cloud` are used together? The comparison MUST operate on the normalized result shape identically regardless of which execution mode produced the current run.
- What happens when an `eval_recipe` path is given for `execution: azd` but more than one recipe exists and no explicit path narrows the choice unambiguously? Auto-discovery MUST only apply when exactly one recipe is discoverable; otherwise the run must not guess.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST dispatch each evaluation run to exactly one of three execution modes -- local, cloud, or azd -- based on the `execution` field resolved from the loaded configuration, defaulting to local when the field is omitted.
- **FR-002**: For local execution, the system MUST invoke the target agent once per dataset row and run the selected evaluators against each response, regardless of whether the target is a Foundry hosted agent, a generic HTTP/JSON agent, or a raw model deployment.
- **FR-003**: For local execution against URL-based agents, the system MUST support the `responses`, `invocations`, and `http-json` protocols and the `json`, `sse`, and `text` response modes, reassembling streamed responses per the configured `stream` aggregation settings before evaluation.
- **FR-004**: For cloud execution, the system MUST only be used with a Foundry prompt (`name:version`) or Foundry hosted agent target with a derivable name and version, MUST sync the dataset to the cloud evaluation service, and MUST capture a cloud run/evaluation identifier for the submitted run.
- **FR-005**: For cloud execution, the system MUST normalize the cloud service's returned output items into the same row/metric result structure produced by local execution.
- **FR-006**: For azd execution, the system MUST delegate to the `azd ai agent eval` command, MUST auto-discover a single `eval.yaml` recipe when `eval_recipe` is not explicitly set and exactly one recipe is discoverable, and MUST normalize the emitted azd metrics into the standard result schema.
- **FR-007**: The system MUST attribute a per-row invocation failure to that specific row's result rather than aborting the entire run, when the failure is isolated to one row.
- **FR-008**: The system MUST persist run artifacts under a timestamped directory within the workspace results area and MUST maintain a pointer to the most recent run's artifacts for downstream tools to consume without knowing the timestamp.
- **FR-009**: The system MUST support an optional publishing step, controlled by the `publish` configuration field for local execution (and implicitly for cloud execution), that uploads results to the appropriate Foundry Evaluations panel without altering the already-persisted local result artifacts if publishing fails.
- **FR-010**: The system MUST reject a dataset with zero rows before invoking any target, for both local and cloud execution, with a clear error rather than completing a run that reports zero rows evaluated.
- **FR-011**: The system MUST keep the resulting run's row/metric schema identical across local, cloud, and azd execution modes so downstream reporting and comparison logic do not need to branch on execution mode.

### Key Entities

- **Execution Mode**: One of `local`, `cloud`, or `azd`, selected from configuration and determining which invocation/evaluation path a run takes.
- **Row Invocation**: The act of calling the target agent (or model deployment) for a single dataset row and capturing its raw response (including streamed text reassembly when applicable).
- **Cloud Run/Evaluation Reference**: The identifier(s) returned by the cloud evaluation service (Foundry via the OpenAI Evals API) that allow a submitted run to be tracked and its output items retrieved.
- **AZD Eval Recipe**: A discoverable or explicitly referenced `eval.yaml`-style recipe consumed by `azd ai agent eval` when `execution: azd` is used.
- **Publish Target**: The Foundry Evaluations panel (Classic or New, depending on execution mode and the `publish` field) that an already-computed run's results may optionally be uploaded to.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Each of the three execution modes (local, cloud, azd) produces a completed run whose row/metric result structure is identical in shape and field names, independent of which mode produced it.
- **SC-002**: 100% of supported local agent kinds (Foundry hosted, generic HTTP/JSON, model deployment) across every supported protocol/response-mode combination are invoked and scored using the same row/metric result shape, with no kind- or protocol-specific fields required by downstream reporting.
- **SC-003**: A single failing row invocation never prevents the remaining rows in the same dataset from being invoked and evaluated; every non-failing row in that run still produces a result.
- **SC-004**: A dataset with zero rows is always rejected with a clear error before any row is invoked, for both local and cloud execution; it never produces a completed run and never reports a false pass.
- **SC-005**: A failed publish step never corrupts or removes the already-persisted local `results.json`/`report.md` for that run; the local artifacts remain exactly as computed before the publish attempt.
- **SC-006**: `execution: cloud` succeeds only for a Foundry prompt or Foundry hosted agent target with a derivable name and version; every other target kind is rejected before any cloud submission.

## Assumptions

- The target agent endpoint (for local execution) is reachable over the network the AgentOps process runs on; network policy and connectivity are the operator's responsibility.
- Cloud execution requires valid Foundry/Azure credentials configured in the environment (see the Evaluation Configuration specification for `project_endpoint` resolution); this specification assumes those credentials, when required, are already available.
- The azd execution path assumes the Azure Developer CLI is installed and its `ai agent eval` capability is available in the environment; installing or configuring azd itself is out of scope.
- Exactly one dataset drives a single evaluation run; concurrent multi-dataset runs from a single `agentops eval run` invocation are not part of this baseline.

## Out of Scope

- The content and structure of the configuration file that selects execution mode (see the Evaluation Configuration specification).
- The schema, thresholds, exit codes, and reporting of the produced results (see the Evaluation Results and Regression specification).
- A standalone `agentops eval compare` command; it is not implemented. Regression comparison during execution is limited to the `--baseline` flag on `agentops eval run`, covered by the Evaluation Results and Regression specification.
- Doctor readiness analysis, release evidence composition, and CI/CD workflow generation, which consume execution results but are specified separately.
- Installing, configuring, or authenticating the Azure Developer CLI (`azd`) itself.

## Implementation Evidence

- `src/agentops/pipeline/orchestrator.py` - end-to-end `eval run` orchestration, including execution-mode dispatch (local/cloud/azd), dataset resolution, and the `exit_code_from()` translation of a run's outcome.
- `src/agentops/pipeline/runtime.py` - local per-row invocation and evaluator execution engine, including evaluator loading and model configuration binding.
- `src/agentops/pipeline/invocations.py` - protocol (`responses`, `invocations`, `http-json`) and response-mode (`json`, `sse`, `text`) handling for URL-based agents, including streaming aggregation.
- `src/agentops/pipeline/cloud_runner.py` and `src/agentops/pipeline/cloud_results.py` - cloud dataset submission via the OpenAI Evals API and normalization of cloud output items back into the standard result schema.
- `src/agentops/pipeline/publisher.py` - Classic/New Foundry Evaluations publishing logic gated by the `publish` configuration field and execution mode.
- `src/agentops/pipeline/azd_runner.py` and `src/agentops/core/azd_eval.py` - azd delegation, `eval.yaml` recipe discovery, and azd metric normalization.
- `tests/unit/test_invocations.py`, `test_cloud_runner.py`, `test_cloud_results.py`, `test_pipeline_publisher.py`, `test_azd_runner.py`, `test_azd_eval.py`, `test_azd_eval_init.py`, `test_runtime_conversation.py`, `test_runtime_dataset_response_source.py`, `test_runtime_model_config.py`, `test_runtime_response_fields.py` - unit coverage of the corresponding execution behaviors above.
- `tests/integration/test_pipeline_smoke.py` (`test_http_pipeline_end_to_end`, `test_http_pipeline_with_baseline`) - end-to-end local HTTP agent execution coverage.
- `docs/how-it-works.md`, `docs/tutorial-hosted-agent.md`, `docs/tutorial-prompt-agent.md` - narrative documentation of the local/cloud/azd execution paths consistent with this baseline.
