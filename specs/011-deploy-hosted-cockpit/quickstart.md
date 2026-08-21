# Quickstart Validation: Deploy Hosted Cockpit

This guide validates the completed feature end to end. It does not replace the
implementation tasks or production deployment documentation.

## Prerequisites

- Python 3.11+ with the repository installed editable.
- Azure CLI authenticated to the target workforce tenant.
- Azure Developer CLI authenticated to the target subscription.
- Permission to deploy App Service resources and create role assignments.
- Permission to read and configure federated credentials on the supplied
  existing app registration.
- Existing single-tenant app registration with:
  - the deterministic Web App callback URI;
  - delegated Azure Monitor Logs permission with admin consent;
  - optional dedicated allowed-group claims configuration.
- Existing Foundry project connected to workspace-based Application Insights.
- At least two projects/sources for multi-project validation when available.

## 1. Run focused automated tests

```powershell
python -m pytest tests\unit\test_cli_cockpit_deploy.py `
  tests\unit\test_cockpit_deployment_selection.py `
  tests\unit\test_cockpit_deployment_preview.py `
  tests\unit\test_cockpit_auth_settings.py `
  tests\unit\test_observe_auth.py `
  tests\unit\test_observe_cache.py `
  tests\unit\test_observe_discovery.py `
  tests\unit\test_observe_queries.py `
  tests\unit\test_observe_service.py `
  tests\unit\test_observe_ui.py -q

python -m pytest tests\integration\test_cockpit_hosted.py `
  tests\integration\test_observe_end_to_end.py -q
```

Expected: all tests pass without Azure credentials because SDK, CLI, azd, and
HTTP interactions are mocked.

## 2. Validate Bicep and packaged deployment assets

```powershell
az bicep build `
  --file src\agentops\templates\cockpit-hosted\infra\main.bicep

agentops cockpit deploy --workspace . --preview
```

Expected preview:

- current workspace project is the default scope;
- only App Service plan, Web App, UAMI, auth settings, non-secret settings,
  Reader, Log Analytics Reader, and one federated credential are listed;
- existing Foundry, Application Insights, Log Analytics, diagnostics, agents,
  models, alerts, and gateways show no planned mutation;
- subscription expansion requires a separate warning and confirmation.

## 3. Deploy the project-default Cockpit

```powershell
agentops cockpit deploy --workspace .
```

Confirm the detected project and preview.

Expected:

- deployment completes through azd;
- the existing app registration is reused;
- the UAMI and exact FIC are created or reused;
- output includes stable Cockpit and Azure portal URLs;
- `/healthz` reports process liveness;
- an anonymous Observe request is denied;
- authenticated `/api/auth/context` returns the expected tenant/user;
- rerunning the command does not duplicate resources, roles, FICs, or change
  the URL.

## 4. Validate local compatibility

```powershell
agentops cockpit --workspace .
```

Expected:

- existing workspace, Doctor, evaluation history, preflight, port reuse, and
  browser behavior remain available;
- the shell loads without querying Azure;
- Observe loads only when opened.

## 5. Validate multi-project Observe

Open Observe in local and hosted modes with equivalent read permissions.

1. Use the default 24-hour range.
2. Apply Foundry resource, project, agent, and model filters.
3. Switch among Overview, Agents, Models and usage, and Telemetry coverage.
4. Trigger manual refresh while another request is in flight.

Expected:

- filters persist in the URL and across views;
- normalized values match between local and hosted modes;
- available source results render within the performance target;
- slow or denied sources become partial coverage results;
- stale requests do not overwrite current filters;
- token values are labeled observed usage;
- last seen is labeled observed activity, not lifecycle status.

## 6. Validate protected content isolation

Use two users with aggregate Cockpit access:

- User A has Privileged Monitoring Data Reader for the protected workspace.
- User B does not.

Open the same trace detail as each user.

Expected:

- both users see aggregate metrics from the UAMI path;
- only User A sees authorized `AppGenAIContent`;
- User B sees `protected_or_unavailable`, never recovered legacy content;
- raw content responses include `Cache-Control: no-store`;
- raw content is absent from shared server caches, page URLs, and browser
  preferences.

## 7. Validate coverage states

Exercise one source for each state:

- resource inaccessible;
- telemetry not configured;
- configured with no data in the period;
- agent/model/token attribution not reported;
- protected content unavailable;
- partial or timed-out query.

Expected: every state has a distinct reason and next action; none is represented
as numeric zero or successful collection.

## 8. Run the full regression suite

```powershell
python -m pytest tests\ -x -q
```

Expected: all established local AgentOps behavior remains green.
