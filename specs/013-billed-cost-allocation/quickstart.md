# Quickstart: Validate Billed Cost Allocation

This guide defines the runnable checks for the implementation. It does not
require Azure credentials for contract, allocation, API, or UI tests.

## Prerequisites

- Python 3.11+
- Repository dependencies installed in the active environment
- Spec dependencies #441 (tools/runs) and #442 (granular model tokens)
  implemented on the branch under test

```powershell
python -m pip install -e .
```

## 1. Validate the cost-model contract

Use the example below as `AGENTOPS_COST_MODEL`. Money and weights are strings;
the model contains no credential or billing API reference.

```powershell
$env:AGENTOPS_COST_MODEL = @'
{
  "version": 1,
  "periods": [
    {
      "id": "2026-08",
      "starts_at": "2026-08-01T00:00:00Z",
      "ends_at": "2026-09-01T00:00:00Z",
      "components": [
        {
          "id": "gpt-ptu-prod",
          "type": "provisioned_throughput",
          "billing_boundary": {
            "kind": "resource",
            "value": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/ai-prod/providers/Microsoft.CognitiveServices/accounts/foundry-prod"
          },
          "billed_source": "August provisioned-throughput commitment",
          "billed_total": "12000.00",
          "currency": "USD",
          "currency_minor_units": 2,
          "allocation_model": "commitment",
          "allocation_key": "weighted_tokens",
          "fallback_key": "total_tokens",
          "token_weights": {
            "input_tokens": "1",
            "output_tokens": "4",
            "cache_read_tokens": "0.25",
            "reasoning_tokens": "4"
          },
          "usage_match": {
            "deployments": ["gpt-prod"]
          }
        },
        {
          "id": "search-prod",
          "type": "search",
          "billing_boundary": {
            "kind": "resource",
            "value": "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/ai-prod/providers/Microsoft.Search/searchServices/search-prod"
          },
          "billed_source": "August Azure AI Search billed total",
          "billed_total": "830.17",
          "currency": "USD",
          "currency_minor_units": 2,
          "allocation_model": "metered",
          "allocation_key": "tool_invocations",
          "usage_match": {
            "tool_names": ["product_search"]
          }
        }
      ]
    }
  ]
}
'@

python -m pytest tests/unit/test_cost_models.py -q
```

Expected:

- the model validates as version 1;
- period and component IDs are stable and unique;
- model/type/key compatibility passes;
- the canonical fingerprint is deterministic;
- no secret-shaped field is accepted.

## 2. Prove exact reconciliation offline

```powershell
python -m pytest tests/unit/test_cost_allocation.py -q
```

Required scenarios:

1. Two agents split a USD commitment by weighted tokens.
2. Two tools split a tool-side total by invocation count.
3. Two runs split compute by active-session seconds.
4. Direct credits take precedence over an explicit credit-event fallback.
5. Missing weighted token classes use total tokens only when configured.
6. A zero denominator leaves the entire amount unallocated.
7. Missing agent/tool/run identity enters the matching unattributed bucket.
8. Largest-remainder tie-breaking is deterministic.
9. Attributed + unattributed + unallocated equals the declared total exactly.
10. Different currencies and different minor-unit precision never combine.
11. Row truncation preserves omitted allocated amount in component summaries.

## 3. Prove configuration failure is isolated

```powershell
python -m pytest tests/unit/test_cockpit_modes.py -q
```

Required outcomes:

- no environment setting produces `absent` and does not alter existing views;
- valid JSON produces `valid`;
- malformed JSON, an overlapping period, an invalid compatibility combination,
  or a payload over 32 KiB produces `invalid`;
- invalid cost configuration produces no cost result but does not prevent
  overview, agents, models, tools, runs, or coverage requests.

## 4. Prove the Observe API and service composition

```powershell
python -m pytest tests/unit/test_observe_models.py tests/unit/test_observe_queries.py tests/unit/test_observe_service.py tests/unit/test_observe_facade.py -q
```

Verify:

- `view: cost` accepts configured period, breakdown, and component selectors;
- the configured period remains authoritative when shared Observe time or
  identity filters are present;
- `cost_agent_key` filters already-allocated rows without changing component
  denominators, amounts, or reconciliation;
- unknown selectors return 422;
- the service queries each required models/tools/runs view at most once, not
  once per component;
- directly reported granular token and credit fields remain null when absent;
- partial source failures lower confidence and produce component coverage;
- a missing total is `not_configured`, never zero;
- unmatched observed allocation capabilities report their allocation key
  without inferring a billing component type;
- result bounds and omitted amounts remain visible;
- no prompt, response, tool argument, tool result, or protected content enters
  a cost payload.

The response shape must match
[contracts/observe-cost-api.openapi.yaml](./contracts/observe-cost-api.openapi.yaml).

## 5. Prove both Cost render paths

```powershell
python -m pytest tests/unit/test_observe_ui.py -q
```

Verify both initial server rendering and JavaScript refresh rendering:

- period, breakdown, and component selectors round-trip through the page URL;
- amounts show currency, method, source, observed numerator/denominator, and
  confidence;
- metered and commitment labels are distinct;
- fallback and partial-coverage explanations are inline;
- missing totals and usage do not render as zero;
- currency subtotals never cross currencies or precision;
- the page states that agent/tool/run breakdowns are alternatives, not
  additive;
- the fixed disclaimer says the figures are operational allocations, not
  invoices or billing-accurate charges.

## 6. Run the local end-to-end fixture

```powershell
python -m pytest tests/integration/test_observe_end_to_end.py -q
```

The fixture must cover:

- agent, tool, and run breakdowns;
- weighted-token preferred allocation and explicit total-token fallback;
- commitment and metered components;
- an unattributed bucket and a fully unallocated component;
- mixed currencies;
- all four confidence states;
- absent and invalid configuration non-regression.

Optionally inspect the implemented UI:

```powershell
agentops cockpit --no-preflight
```

Open the printed local URL, select **Cost**, and confirm the same fixture
outcomes without using query syntax or raw resource IDs.

## 7. Verify hosted deployment remains least privilege

```powershell
python -m pytest tests/unit/test_cockpit_deployment_preview.py tests/unit/test_cockpit_hosted_templates.py -q
```

Then preview a hosted deployment in a configured test workspace:

```powershell
agentops cockpit deploy --preview
```

The preview must show:

- optional `AGENTOPS_COST_MODEL` as a non-secret app setting when configured;
- no new Azure resource;
- only the existing `Reader` and `Log Analytics Reader` assignments;
- no Cost Management, billing, contributor, or write-capable role;
- no credential, token, connection string, or secret.

Do not execute a deployment for this validation.

## 8. Run the full suite

```powershell
python -m pytest tests/ -x -q
```

The feature is ready for review only when focused and full-suite tests pass,
the contracts remain additive, and removing `AGENTOPS_COST_MODEL` restores the
existing Cockpit behavior exactly.
