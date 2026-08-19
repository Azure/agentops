# Feature Specification: Azure Storage Evaluation Datasets

**Feature Branch**: `placerda-azure-storage-datasets`

**Created**: 2026-08-18

**Status**: Draft

**Input**: User description: "https://github.com/Azure/agentops/issues/430"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run an Evaluation from Azure Storage (Priority: P1)

As an agent engineer, I want to reference an evaluation dataset stored in Azure
Storage so that I can run an AgentOps evaluation without first downloading and
maintaining a local copy.

**Why this priority**: Direct remote loading is the core value of the feature
and removes the manual synchronization step described in the issue.

**Independent Test**: Configure an otherwise valid evaluation to use a dataset
in Azure Blob Storage or Azure Data Lake Storage Gen2, run the evaluation, and
confirm that the same rows and evaluation outcomes are produced as when an
equivalent local dataset is used.

**Acceptance Scenarios**:

1. **Given** an accessible dataset in Azure Blob Storage, **When** a user runs
   the evaluation, **Then** AgentOps loads the dataset directly and evaluates
   every valid row without requiring a user-managed local copy.
2. **Given** an accessible dataset in Azure Data Lake Storage Gen2, **When** a
   user runs the evaluation, **Then** AgentOps loads the dataset directly and
   evaluates every valid row.
3. **Given** a local dataset configuration that worked before this feature,
   **When** the user runs the evaluation, **Then** its behavior and outputs
   remain unchanged.

---

### User Story 2 - Authenticate Securely in Local and Automated Runs (Priority: P2)

As an enterprise platform engineer, I want remote datasets to use approved
Azure authentication methods so that developers and automated pipelines can
access centrally governed data without embedding credentials in project files.

**Why this priority**: Remote loading is not usable in enterprise environments
unless it works with local developer identities, workload identities, and
managed identities already used by AgentOps.

**Independent Test**: Run the same remote-dataset evaluation after `az login`
and in an automated environment using its existing Azure identity, then verify
that AgentOps accesses storage without any dataset-specific token or credential
configuration.

**Acceptance Scenarios**:

1. **Given** a user signed in through an approved Azure developer identity,
   **When** the identity has read access to the dataset, **Then** AgentOps loads
   the dataset without requesting a storage key.
2. **Given** an automated workload with an approved managed or service
   identity, **When** it has read access, **Then** the pipeline loads the
   dataset without a manual sign-in.
3. **Given** an identity without read access, **When** the evaluation starts,
   **Then** AgentOps stops before evaluating rows and reports an actionable
   authorization error that identifies the source and the required storage
   permission.

---

### User Story 3 - Diagnose Remote Dataset Readiness (Priority: P3)

As an agent engineer, I want analysis and runtime failures to distinguish
configuration, access, connectivity, and content problems so that I can correct
the dataset source quickly.

**Why this priority**: Cloud sources introduce failure modes that do not exist
for local files, and clear diagnostics are necessary for reliable CI/CD use.

**Independent Test**: Exercise missing objects, malformed references, denied
access, unavailable storage, and invalid dataset content, then verify that each
case returns a distinct, actionable diagnosis before any partial evaluation is
reported as successful.

**Acceptance Scenarios**:

1. **Given** a malformed or unsupported remote dataset reference, **When** the
   user analyzes or runs the evaluation, **Then** AgentOps identifies the
   invalid reference and explains the accepted source forms.
2. **Given** a valid reference to a missing object, **When** the evaluation
   starts, **Then** AgentOps reports that the object was not found and does not
   produce success-shaped results.
3. **Given** a readable object whose content does not satisfy the evaluation
   dataset contract, **When** AgentOps validates it, **Then** the user receives
   the same row and field guidance available for an invalid local dataset.
4. **Given** a transient connectivity failure, **When** AgentOps cannot obtain
   the dataset, **Then** it reports the source and failure category without
   exposing credentials or claiming that evaluation completed.

### Edge Cases

- The remote object is empty or contains no valid evaluation rows.
- The object changes while an evaluation is running; one resolved content
  snapshot must be used consistently for the entire run.
- The dataset reference contains a query string, embedded credential, or other
  unsupported authentication material.
- The authenticated identity can reach the account but cannot read the
  container, file system, directory, or object.
- The storage account is reachable only from an approved private network, but
  the current runner is outside that network.
- The object is larger than the supported evaluation input limit or cannot be
  fully obtained.
- The remote content uses a file type or encoding that AgentOps does not support
  for local evaluation datasets.
- The remote source is accessible during readiness analysis but becomes
  unavailable before execution.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: AgentOps MUST accept an Azure Storage dataset reference wherever
  the existing evaluation configuration accepts a local dataset reference.
- **FR-002**: AgentOps MUST support dataset objects stored in Azure Blob Storage
  and Azure Data Lake Storage Gen2.
- **FR-003**: AgentOps MUST preserve existing local dataset behavior, including
  validation, evaluator selection, evaluation outputs, and exit-code meanings.
- **FR-004**: A remote dataset MUST satisfy the same content and row contract as
  an equivalent local dataset; remote storage MUST NOT make invalid content
  eligible for evaluation.
- **FR-005**: AgentOps MUST resolve a remote dataset once per evaluation run and
  use that resolved content consistently for all rows in the run.
- **FR-006**: AgentOps MUST support identity-based access suitable for both
  signed-in developers and automated workloads, including managed identity,
  Azure CLI identity, and service principal scenarios.
- **FR-007**: AgentOps MUST use the same Azure authentication context already
  used by AgentOps and MUST NOT require a dataset-specific token, storage key,
  connection string, or SAS.
- **FR-008**: AgentOps MUST reject remote dataset references containing query
  strings, embedded credentials, or other authentication material.
- **FR-009**: AgentOps MUST identify the remote source in user-facing
  diagnostics and run provenance using the validated reference, which is sufficient
  to distinguish the account, container or file system, path, and object.
- **FR-010**: AgentOps MUST validate the source form before evaluation and
  distinguish malformed references, unsupported sources, missing objects,
  authorization failures, connectivity failures, and invalid dataset content.
- **FR-011**: AgentOps MUST stop with the established runtime or configuration
  error outcome when a remote dataset cannot be resolved or validated; it MUST
  NOT emit a successful or threshold-failure result for an evaluation that did
  not run.
- **FR-012**: Readiness analysis MUST report whether a configured remote source
  is structurally valid and whether access or content validation could be
  completed in the current environment.
- **FR-013**: Remote dataset support MUST work in interactive development and
  non-interactive CI/CD runs without requiring a separate user-managed download
  step.
- **FR-014**: User guidance MUST document supported source forms,
  Azure identity behavior, required read permissions, private-network
  considerations, and common remediation steps.
- **FR-015**: The configuration change enabling remote datasets MUST be
  additive and MUST NOT require a new command or change the meaning of an
  existing command or exit code.

### Key Entities

- **Dataset Reference**: The user-provided location of an evaluation dataset,
  either a local path or a remote Azure Storage location. For remote sources it
  identifies the storage service, account, container or file system, and object
  path, with sensitive values excluded from display.
- **Resolved Dataset**: The validated, immutable-for-the-run content used to
  produce evaluation rows. It follows the same dataset contract regardless of
  where the source is stored.
- **Access Context**: The approved Azure identity already available to the
  current interactive or automated run. Authentication details are never part
  of normalized evaluation artifacts.
- **Source Provenance**: Validated metadata that identifies the source used for
  a run and enables troubleshooting without exposing authentication details.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user with read access can configure and run an evaluation from
  either supported Azure Storage service without performing a manual download.
- **SC-002**: For equivalent local and remote datasets, 100% of dataset rows,
  validation outcomes, selected evaluators, and evaluation results are
  equivalent apart from source provenance.
- **SC-003**: All supported authentication scenarios complete using the Azure
  identity already available to AgentOps, with zero dataset-specific tokens or
  credentials required.
- **SC-004**: In tests covering malformed references, missing objects, denied
  access, connectivity failures, and invalid content, 100% of failures are
  classified with a distinct remediation message and none are reported as a
  completed evaluation.
- **SC-005**: Existing local-dataset evaluation scenarios continue to complete
  with no user action and no observable behavior regression.
- **SC-006**: In a standard connected environment, 95% of evaluations using a
  remote dataset of up to 100 MiB begin row processing within 30 seconds of the
  run command.
- **SC-007**: At least 90% of first-time users in an acceptance exercise can
  configure a permitted remote dataset and reach either a successful run or a
  precise access diagnosis without external assistance.
- **SC-008**: Remote references containing query strings, embedded credentials,
  storage keys, connection strings, or SAS are rejected before any network
  request.

## Assumptions

- The first release adds remote location support, not new dataset content
  formats; a remote object must use a format already supported for local
  evaluation datasets at the time of the run.
- Each evaluation references one primary dataset object. Loading an entire
  container, directory, repository, or collection of files as one dataset is
  outside this feature's initial scope.
- Azure Storage access is read-only. AgentOps does not create, modify, copy, or
  delete remote dataset objects.
- Azure identity is the only supported authentication model for remote
  datasets. Local development uses the current `az login` identity; automated
  environments use their existing managed, workload, or service principal
  identity.
- Storage account provisioning, role assignment, firewall changes, private
  endpoint creation, and credential issuance remain external administrative
  responsibilities.
- Existing evaluation output and release-evidence contracts remain stable;
  validated source provenance may be added only through backward-compatible
  evolution.
- Network throughput and storage service availability affect retrieval time;
  the measurable performance target assumes a healthy supported Azure region
  and runner network.
