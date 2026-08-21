# Implementation Plan: Deploy Hosted Cockpit

**Branch**: `placerda-specify-issue-433` | **Date**: 2026-08-20 |
**Spec**: [spec.md](spec.md)

**Input**: Feature specification from
`/specs/011-deploy-hosted-cockpit/spec.md`

## Summary

Add `agentops cockpit deploy` as an explicit, guided Azure deployment while
preserving `agentops cockpit` as the unchanged local entry point. The command
resolves the current workspace project as the default scope, validates an
existing single-tenant Entra app registration, previews every resource and role
assignment, materializes an azd-compatible deployment bundle, provisions a
Linux Azure App Service with a dedicated user-assigned managed identity, and
deploys the hosted FastAPI Cockpit.

The running Cockpit remains read-only. It uses the managed identity for Azure
Resource Graph discovery and aggregate Azure Monitor queries, and uses the
signed-in user's delegated identity through OBO only for explicit
`AppGenAIContent` detail requests. Local and hosted modes share one Observe
service, normalized contracts, API, and browser UI. Hosted mode omits local
history surfaces rather than presenting them as empty cloud data.

## Technical Context

**Language/Version**: Python 3.11+

**Primary Dependencies**: Typer, FastAPI, Uvicorn, Pydantic v2,
`azure-identity`, `azure-monitor-query`, `azure-mgmt-resourcegraph`,
`azure-ai-projects`, Azure CLI, Azure Developer CLI, Bicep

**Storage**: No database. One versioned scope contract is stored as the
non-secret `AGENTOPS_OBSERVE_SCOPE` App Service setting. Applied filters live in
the page URL and optional browser storage. Discovery and query results use
bounded in-process TTL caches only.

**Testing**: pytest with mocked Azure SDK, Azure CLI, azd, HTTP, and time;
Bicep build/lint and azd dry-run/what-if coverage using existing tooling

**Target Platform**: Linux Azure App Service for hosted mode; existing
Windows/Linux/macOS local CLI behavior remains supported

**Project Type**: Python CLI plus FastAPI web application and packaged
azd/Bicep deployment templates

**Performance Goals**: For up to 10 readable telemetry sources, 95% of Overview
requests return available aggregates or actionable partial results within
10 seconds; the active view refreshes every five minutes; result and discovery
TTL values are two and 15 minutes respectively.

**Constraints**: Runtime is stateless and read-only; no stored credentials,
workspace keys, telemetry connection strings, or app-registration secrets;
single workforce tenant only; no persistent raw-content cache; bounded queries
and result sets; AppGenAIContent protected-table behavior is a public-preview
dependency that must be revalidated before release.

**Scale/Scope**: One hosted Cockpit per configured Observe boundary; modes cover
explicit project IDs, one Foundry resource, one resource group, or one
subscription; up to 10 active telemetry sources per request; one optional Entra
allowed group.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle or gate | Design evidence | Status |
|---|---|---|
| Preserve public contracts | `cockpit` becomes a Typer group with a no-argument callback that preserves the existing local command; `deploy` is additive; exit codes remain 0/1/2. | PASS |
| Enforce architectural boundaries | CLI only parses/renders; deployment orchestration is in `services/`; pure scope and API models are in `core/`; runtime Observe behavior remains under `agent/`. | PASS |
| Isolate Azure runtime integration | Azure imports remain lazy; local tests use mocks; all `DefaultAzureCredential` construction preserves `process_timeout=30`. | PASS |
| Keep release evidence trustworthy | Only the explicit deployment service mutates the Cockpit's own hosting, authentication, UAMI federation, and read-role assignments. Hosted routes expose no provisioning capability and perform only reads against monitored resources. | PASS |
| Verify every behavior change | Focused unit, contract, integration, hosted-auth, rerun, partial-failure, and local-regression tests are identified below. | PASS |
| Deployment preview | The CLI runs azd/Bicep preview and appends the planned federated credential and role assignments before confirmation. | PASS |
| Resource and role enumeration | The plan creates only App Service plan, Web App, UAMI, auth settings, non-secret app settings, and read-only assignments. Existing Foundry and telemetry resources are referenced, never mutated. | PASS |
| Runtime mutation prohibition | Hosted application code receives no deployment commands or write-role credentials; UAMI receives Reader and Log Analytics Reader only, never Privileged Monitoring Data Reader. | PASS |

### Post-design re-check

Phase 1 contracts preserve the local CLI callback, make the authorization
boundary explicit, reject out-of-bound filters, isolate raw-content access in a
non-cacheable OBO endpoint, and enumerate all deployment mutations. No
constitutional exception is required.

## Project Structure

### Documentation (this feature)

```text
specs/011-deploy-hosted-cockpit/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── cli.md
│   ├── observe-api.openapi.yaml
│   └── observe-scope.schema.json
└── tasks.md
```

### Source Code (repository root)

```text
src/agentops/
├── cli/
│   └── app.py
├── core/
│   └── observe.py
├── services/
│   └── cockpit_deployment.py
├── agent/
│   ├── cockpit.py
│   └── observe/
│       ├── __init__.py
│       ├── auth.py
│       ├── cache.py
│       ├── discovery.py
│       ├── queries.py
│       ├── service.py
│       └── ui.py
├── utils/
│   └── foundry_discovery.py
└── templates/
    └── cockpit-hosted/
        ├── azure.yaml
        ├── app/
        │   ├── main.py
        │   └── requirements.txt.tmpl
        └── infra/
            ├── main.bicep
            └── main.parameters.json

tests/
├── integration/
│   ├── test_cockpit_hosted.py
│   └── test_observe_end_to_end.py
└── unit/
    ├── test_cli_cockpit_deploy.py
    ├── test_cockpit_deployment.py
    ├── test_observe_auth.py
    ├── test_observe_cache.py
    ├── test_observe_discovery.py
    ├── test_observe_queries.py
    ├── test_observe_service.py
    └── test_observe_ui.py

docs/
├── observe.md
├── how-it-works.md
├── operate.md
└── deploy-hosted-cockpit.md
```

**Structure Decision**: Keep one Python package. Pure Pydantic contracts and
scope validation live in `core/observe.py`; Azure and OBO integrations are
isolated in the `agent/observe/` runtime package; explicit cloud mutation is
isolated in `services/cockpit_deployment.py`; packaged azd/Bicep assets are
materialized under `.agentops/deploy/cockpit/` at runtime and remain local-only.
The existing `cockpit.py` remains the FastAPI composition root and local
compatibility surface.

The public documentation entry point remains
`https://aka.ms/agentops-accelerator`. `docs/operate.md` gains a concise hosted
Cockpit subsection describing the sensitive-content access model and linking to
`docs/deploy-hosted-cockpit.md`, which contains the complete deployment,
identity, RBAC, protected-table, preview-status, migration, troubleshooting,
recovery, and rerun guidance.

## Implementation Design

### 1. Preserve the CLI and add explicit deployment

- Replace the flat `@app.command("cockpit")` registration with a `cockpit_app`
  Typer group whose callback executes the existing local behavior when no
  subcommand is supplied.
- Add `cockpit_app.command("deploy")`; the handler delegates all discovery,
  preview, confirmation, azd execution, federation, and health checks to
  `services/cockpit_deployment.py`.
- Preserve the existing local arguments, port-conflict behavior, preflight, and
  browser launch exactly. Preserve exit code `1` for configuration/runtime
  errors and `0` for a completed preview or deployment; no readiness threshold
  is evaluated, so exit code `2` is not introduced.
- Support guided prompts by default and explicit flags for automation as defined
  in [contracts/cli.md](contracts/cli.md). `--yes` is rejected unless every
  required input is supplied non-interactively.

### 2. Deployment state machine and preview

1. Resolve the current workspace, active azd environment, Foundry endpoint, and
   canonical project resource ID. If exactly one project cannot be resolved,
   require explicit project selection.
2. Validate Azure CLI and azd authentication, target subscription, workforce
   tenant, existing app registration, redirect URI, delegated Log Analytics
   consent, optional group, and deployer ARM/Graph permissions.
3. Build `ObserveScope` with project as the default. Any Foundry, resource-group,
   or subscription expansion requires a new preview; subscription mode displays
   an additional warning.
4. Materialize the version-matched deployment bundle under
   `.agentops/deploy/cockpit/`, set azd environment values, and run
   `azd provision --preview`.
5. Merge the Bicep preview with the planned app-registration federated
   credential and exact UAMI read-role assignments. Require confirmation.
6. Run `azd provision`, idempotently create or reuse the UAMI federated
   credential through `az ad app federated-credential`, then run `azd deploy`.
7. Verify anonymous liveness, protected auth context, UAMI aggregate-read access,
   and effective configuration before returning the Cockpit and Azure portal
   URLs. RBAC propagation retries are bounded and reported separately from app
   deployment failures.

The deployment service writes a versioned, non-secret journal to
`.agentops/deploy/cockpit/deployment-state.json`. Each entry records the planned
mutation, whether the target pre-existed the attempt, the last completed stage,
and resulting resource IDs. Failures preserve cloud state by default; only
current-attempt local temporary files may be rolled back automatically. A rerun
reconciles the journal with live ARM and Graph state, regenerates the preview,
and resumes at the first incomplete or mismatched stage. Destructive cloud
cleanup requires a separate future command with its own preview and confirmation
and is not part of this MVP.

### 3. Azure resources and permissions

Bicep creates or updates only:

- one Linux App Service plan;
- one Linux Web App;
- one dedicated user-assigned managed identity attached to the Web App;
- `authsettingsV2` for single-tenant Entra Easy Auth;
- non-secret settings including `AGENTOPS_COCKPIT_MODE=hosted`,
  `AGENTOPS_OBSERVE_SCOPE`, tenant/client IDs, UAMI client ID, and optional
  allowed-group ID;
- deterministic `Reader` and `Log Analytics Reader` role assignments.

`Reader` is assigned at the configured discovery boundary. `Log Analytics
Reader` is assigned only to linked telemetry resources/workspaces discovered
from the selected projects or contained by the selected parent boundary. Every
derived assignment is shown in the preview. The UAMI is never granted
`Privileged Monitoring Data Reader`.

The existing app registration is not created. The CLI validates it and
idempotently configures only the named UAMI federated credential after explicit
confirmation. Missing redirect URI or delegated consent blocks deployment with
remediation; the MVP does not silently patch unrelated app-registration
properties.

### 4. Hosted authentication and OBO

- Easy Auth rejects unauthenticated requests before FastAPI except `/healthz`.
- `agent/observe/auth.py` validates `x-ms-client-principal`, tenant, audience,
  and optional group defense-in-depth before returning user context.
- Aggregate endpoints always use `ManagedIdentityCredential(client_id=...)`.
- The raw Easy Auth access token is accepted only as the OBO user assertion.
  `OnBehalfOfCredential` uses a callable that obtains a UAMI
  `api://AzureADTokenExchange/.default` assertion, then requests the delegated
  Azure Monitor Logs scope.
- Only `/api/observe/trace-content` receives the delegated credential. Its
  responses set `Cache-Control: no-store`, are excluded from shared caches, and
  never place content in URLs or browser persistence.

### 5. Discovery and query execution

- Use Azure Resource Graph to enumerate Foundry account/project ARM resources
  inside `ObserveScope`.
- Reuse and extend `utils/foundry_discovery.py` and
  `AIProjectClient.connections.list()` to resolve each project's linked
  Application Insights resource ID without requesting connection secrets.
- Resolve workspace-based Application Insights resources to their Log Analytics
  workspaces. Deduplicate shared workspaces while preserving every originating
  resource/project attribution.
- Cache discovery for 15 minutes by scope contract and credential identity.
- Use async `LogsQueryClient.query_batch()` with one bounded query per source so
  full, partial, throttled, timed-out, denied, and empty results remain
  source-addressable.
- Filter time and dimensions before aggregation, summarize in KQL, limit detail
  results, use a 30-second per-query server timeout and a 10-second Overview
  request deadline, and suppress results for superseded browser requests.
- Cache non-sensitive results for two minutes by scope, view, filters, and time
  range. Never cache `AppGenAIContent`.

### 6. API and UI composition

- Add Observe routes from
  [contracts/observe-api.openapi.yaml](contracts/observe-api.openapi.yaml).
- `create_app(workspace: Path | None, mode: Literal["local", "hosted"])` loads
  the shell without Azure queries. Local mode composes existing workspace
  sections plus Observe; hosted mode composes only cloud-safe navigation and
  Observe.
- The browser owns draft/applied filters, writes applied non-sensitive filters
  to the URL, and sends a normalized request body to the API.
- Use one accessible UI for Overview, Agents, Models and usage, and Telemetry
  coverage. Render available partial results immediately, label source and
  refresh time, and use abortable fetch requests so stale responses cannot
  overwrite the current view.

### 7. Test strategy

- **CLI regression**: existing `agentops cockpit` invocation, options, help,
  port reuse, preflight, and browser launch remain unchanged.
- **Deployment unit tests**: project-default scope, explicit expansion,
  subscription warning, preview-before-mutation, app-registration validation,
  Graph/FIC idempotency, deterministic role IDs, azd failures, health failures,
  rerun stability, and no duplicate assignments.
- **Contract tests**: validate scope JSON against the Pydantic model and JSON
  schema; validate representative API responses against OpenAPI.
- **Auth tests**: missing/invalid Easy Auth headers, wrong tenant/group, OBO
  assertion construction, UAMI-only aggregate path, user-only content path,
  and no-store headers.
- **Observe tests**: multi-project discovery, shared-workspace deduplication,
  normalization, missing dimensions, protected-table empty ambiguity, bounded
  fan-out, timeout, partial results, cancellation suppression, TTL keys, and raw
  content never entering shared caches.
- **Integration tests**: equivalent local/hosted normalized data with identical
  fake Azure sources; hosted startup without workspace files; local history
  remains local; anonymous data routes denied; liveness remains available.
- **IaC tests**: Bicep build/lint, what-if snapshot, read-only role allowlist,
  auth required, UAMI retained on rerun, and absence of telemetry-resource
  creation or mutation.

## Complexity Tracking

No constitutional violations require justification.
