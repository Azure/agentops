# Quickstart: Validate User and Department Attribution

This is a validation guide for the implementation, not an implementation
guide. Model rules are defined in [data-model.md](./data-model.md); wire shapes
are defined in the
[configuration schema](./contracts/attribution-config.schema.json) and
[Observe API delta](./contracts/observe-attribution-api.openapi.yaml).

Most checks are offline and require neither Azure credentials nor real identity
data. Use synthetic identities only in fixtures.

## Prerequisites

- Python 3.11+
- Repository dependencies installed in the active environment
- The billed-cost allocation contract from spec 013 implemented for cost
  scenarios
- An authenticated, in-scope Azure test deployment only for the optional manual
  Cockpit and deployment-preview checks

```powershell
python -m pip install -e .
```

## 1. Validate disabled parity and strict configuration

Start with no attribution setting:

```powershell
Remove-Item Env:AGENTOPS_ATTRIBUTION_CONFIG -ErrorAction SilentlyContinue
python -m pytest tests/unit/test_attribution_models.py tests/unit/test_cockpit_modes.py -q
```

Expected:

- configuration load state is `absent`;
- existing Observe queries, filters, payloads, cache behavior, authorization,
  deployment behavior, and navigation are unchanged;
- the attribution endpoint and UI controls are unavailable;
- no `user_attribution` coverage entry appears.

Then validate an enabled bootstrap configuration with no mappings:

```powershell
$env:AGENTOPS_ATTRIBUTION_CONFIG = @'
{
  "version": 1,
  "enabled": true,
  "deployment_namespace": "11111111-2222-4333-8444-555555555555",
  "generation": 1,
  "departments": []
}
'@

python -m pytest tests/unit/test_attribution_models.py tests/unit/test_cockpit_modes.py -q
```

Required configuration cases:

1. Valid version 1 JSON loads as `valid`.
2. `enabled: false` loads as `disabled`.
3. Missing namespace/generation while enabled is invalid.
4. Unknown fields, secret-shaped fields, duplicate department IDs, duplicate
   cross-department user keys, and duplicate cross-department group IDs fail.
5. More than 100 departments, 500 total user keys, 100 total group IDs, or
   64 KiB encoded JSON fails.
6. A user key whose generation differs from the top-level generation fails.
7. Reordered but semantically identical JSON produces the same fingerprint.
8. Invalid attribution configuration does not block non-attribution views.
9. Error and log-safe representations do not contain mapping values.

The implemented model must agree with
[contracts/attribution-config.schema.json](./contracts/attribution-config.schema.json).

## 2. Prove identity eligibility and pseudonym behavior

```powershell
python -m pytest tests/unit/test_attribution_models.py tests/unit/test_observe_queries.py tests/unit/test_observe_adapters.py -q
```

Required cases:

- non-empty `UserAuthenticatedId` is eligible;
- non-empty OpenTelemetry `enduser.id` is eligible;
- equal eligible aliases resolve to one identity;
- conflicting eligible aliases are `ambiguous` and remain unattributed;
- `UserId`, `enduser.pseudo.id`, session, device, browser, IP, prompt, and
  behavior values are never fallbacks;
- raw identity is removed from every aggregate KQL projection;
- identical namespace, generation, tenant, and identity produce identical full
  SHA-256 keys in Python and KQL;
- changing deployment namespace or tenant changes the key;
- changing generation invalidates old keys and tokens;
- ordinary restart and version deployment with unchanged configuration preserve
  keys.

Search the implemented aggregate query assertions for a raw identity projection.
Any aggregate response that contains one is a failure, even when every unit test
otherwise passes.

## 3. Prove department mapping and coverage

After obtaining a synthetic pseudonymous key from a protected test fixture,
validate a mapped configuration:

```powershell
$env:AGENTOPS_ATTRIBUTION_CONFIG = @'
{
  "version": 1,
  "enabled": true,
  "deployment_namespace": "11111111-2222-4333-8444-555555555555",
  "generation": 1,
  "departments": [
    {
      "id": "engineering",
      "label": "Engineering",
      "user_keys": [
        "usr1.g1.aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
      ],
      "group_ids": []
    },
    {
      "id": "support",
      "label": "Support",
      "user_keys": [],
      "group_ids": ["aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"]
    }
  ]
}
'@

python -m pytest tests/unit/test_observe_adapters.py tests/unit/test_observe_service.py tests/unit/test_observe_principal.py -q
```

Expected:

- explicit pseudonymous-user mapping wins over group claims;
- group claims classify only the exact signed-in principal;
- group claims never classify another telemetry user;
- multiple applicable group departments are ambiguous and remain unattributed;
- group-claim overage performs no directory lookup and produces partial coverage;
- one bounded mapping `datatable` is used per source, never one query per user;
- usage grouped by department plus the separate unattributed summary equals the
  unfiltered usage total exactly;
- coverage distinguishes `available`, `partial`, `not_reported`,
  `inaccessible`/`protected_or_unavailable`, `ambiguous`, and `error` per source;
- a source failure does not hide successful attributed or unattributed evidence
  from another source.

## 4. Prove delegated access, singleton escalation, and opaque filters

```powershell
python -m pytest tests/unit/test_observe_facade.py tests/unit/test_observe_principal.py tests/unit/test_observe_ui.py tests/unit/test_cockpit_modes.py -q
```

Required cases:

1. Every user list and user-token request uses only a fresh delegated OBO
   credential.
2. Department cardinality is classified before shared caching.
3. If any returned department has one active user, the aggregate result is
   discarded and the complete request is rerun through delegated access.
4. A missing/failed delegated assertion returns protected/unavailable evidence
   and never retries with deployment identity.
5. Delegated responses report cache bypass and
   `Cache-Control: private, no-store`.
6. Safe multi-user department aggregates alone may enter `ObserveCache`.
7. Raw identity appears only in the current delegated Users response and never
   in a URL, browser storage, cookie, shared cache, log, configuration, Doctor
   output, or evidence pack.
8. User tokens are principal-, scope-, semantic-config-, and generation-bound.
9. Department tokens are scope-, semantic-config-, and generation-bound.
10. Malformed, stale, changed-mapping, cross-scope, and cross-principal tokens
    return a 422-style closed error before querying and never produce a broader
    response.
11. Valid user and department tokens compose with existing Observe filters by
    logical AND and round-trip through the page URL.

The response must agree with
[contracts/observe-attribution-api.openapi.yaml](./contracts/observe-attribution-api.openapi.yaml).

## 5. Prove the 499-plus-Other bound and reconciliation

```powershell
python -m pytest tests/unit/test_observe_queries.py tests/unit/test_observe_service.py tests/unit/test_observe_models.py -q
```

Use fixtures with 501 or more identified users and separate missing/ambiguous
identity records.

Expected:

- rows are ranked by invocations for usage and final allocated minor-unit amount
  for cost;
- ties use pseudonymous key ascending;
- exactly 499 users and one `Other users` row are returned;
- `rows_shown` is 500, `truncated` is true, and the exact distinct-user and
  omitted-user counts are retained;
- `Other users` contains every omitted identified user and no unattributed
  identity bucket;
- individual rows plus `Other users` plus the separate unattributed summary
  reconcile to the complete unfiltered total;
- zero or null source measurements preserve their original meaning.

## 6. Prove cost attribution does not redistribute billed totals

```powershell
python -m pytest tests/unit/test_cost_allocation.py tests/unit/test_observe_service.py tests/unit/test_observe_facade.py -q
```

Required cases:

- cost attribution requires one configured period and one component;
- one response ranks only one declared pool, currency, precision, method, and
  denominator;
- full-period allocation runs before user/department filtering;
- filtering never changes any preallocated user amount or denominator;
- `Other users` sums already allocated minor-unit amounts and never reruns
  allocation;
- attributed + unattributed + unallocated equals the selected component's
  declared total exactly;
- missing cost allocation evidence is distinct from available usage attribution;
- an unknown period/component or invalid identity token fails closed.

## 7. Prove API, UI, and end-to-end behavior

```powershell
python -m pytest tests/unit/test_observe_ui.py tests/unit/test_cockpit_modes.py tests/integration/test_observe_end_to_end.py tests/integration/test_cockpit_hosted.py -q
```

The combined fixture must cover:

- absent, disabled, valid-empty, valid-mapped, and invalid configuration;
- complete, partial, missing, conflicting, and inaccessible identity evidence;
- department aggregation and filtering;
- delegated user bootstrap and drill-down;
- singleton department escalation;
- copied/stale filter failure;
- more than 500 users;
- restart stability and explicit rotation;
- usage with cost present and absent;
- identical server-rendered and JavaScript-refreshed labels, totals, filters,
  coverage, and protected-state behavior.

Optional manual inspection in a synthetic Azure test deployment:

```powershell
agentops cockpit --no-preflight
```

Open the printed URL. In the delegated Users view, confirm a synthetic raw
identity appears beside its pseudonymous key. Apply it to a department only by
editing the deployment configuration, restart, and confirm the Department view
uses the mapping. Rotate to a fresh namespace and generation and confirm the old
URL fails closed.

## 8. Verify deployment remains least privilege and redacted

```powershell
python -m pytest tests/unit/test_cockpit_deployment_preview.py tests/unit/test_cockpit_hosted_templates.py tests/unit/test_evidence_pack.py -q
```

Then, in a configured synthetic test workspace, preview without deploying:

```powershell
agentops cockpit deploy --preview
```

The preview must:

- warn that attribution widens delegated views to individual usage and cost and
  still require the existing confirmation;
- show only attribution state, generation, fingerprint, department-definition
  count, user-key entry count, and group-ID entry count, never its value;
- add only the optional App Service setting;
- retain exactly the existing resources and `Reader` / `Log Analytics Reader`
  assignments;
- add no Microsoft Graph permission, directory read, Key Vault, secret, role, or
  write capability;
- leave the deployed state unchanged if confirmation is declined;
- exclude mapping values, raw identities, user rows, and filter tokens from
  deployment journals and release evidence.

Do not execute a deployment for this validation.

## 9. Validate operator time and standard-scope display performance

### SC-005 representative-operator protocol

Use a fixed synthetic dataset and at least ten representative operators who do
not know the answer. For each participant:

1. Show the same unfiltered department Usage view and start one stopwatch.
2. Ask the participant to identify and filter to the highest-consuming
   department without exporting data.
3. Ask them to copy the opaque filtered URL, open it in a new private window
   while signed in, and confirm the same department and time range.
4. Stop the timer only after restoration is confirmed.
5. Record an anonymous participant ID, timestamps, both correctness outcomes,
   elapsed seconds, and pass/fail. Do not retain the copied URL.

A participant passes only when both outcomes are correct within 120 seconds.
SC-005 passes when at least 90% of participants pass.

### SC-006 standard-scope protocol

The controlled standard scope is one project, three representative telemetry
sources, 200 synthetic users, and 100 departments. After one warm-up, measure
20 department Usage displays and 20 department Cost displays from bounded
aggregate response receipt through completed HTML rendering. Use nearest-rank
p95 (sample 19 after sorting 20 durations). Each view type passes at p95 <= 5s.

Run the repeatable, Azure-free acceptance test:

```powershell
python -m pytest tests/unit/test_attribution_performance_acceptance.py -q
```

Record the environment, commit, sample count, sorted durations, p95, and result.
This test isolates application display cost. Repeat the same sample protocol in
the target connected environment before release to include discovery, Azure
Monitor query, network, and browser latency.

## 10. Run the full suite

```powershell
python -m pytest tests/ -x -q
```

The feature is ready for review only when the focused and full suites pass,
contracts remain additive, reconciliation is exact, and removing
`AGENTOPS_ATTRIBUTION_CONFIG` restores existing behavior.
