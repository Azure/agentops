# Quickstart: Validating Granular Token Classes in the Models View

**Feature**: `012-granular-token-dimensions` | **Date**: 2026-08-24
**References**: [spec.md](./spec.md) · [plan.md](./plan.md) · [data-model.md](./data-model.md) ·
[contracts/](./contracts/)

This is a **validation guide**, not an implementation guide. It lists the scenarios that prove
the feature works end to end and maps each one to the success criteria it satisfies.
Implementation details belong in `tasks.md`.

---

## Prerequisites

```bash
python -m pip install -e .
python -m pip install pytest
```

Azure credentials are **not** required for any scenario in the "Offline validation" section.
Only the "Live cockpit validation" section needs a workspace with telemetry.

---

## Offline validation (no Azure credentials)

These scenarios exercise the whole feature over synthetic telemetry rows and are the primary
gate. Run them first.

### Command

```bash
python -m pytest tests/unit/test_observe_queries.py tests/unit/test_observe_service.py tests/unit/test_observe_ui.py -q
```

Full suite before opening a pull request:

```bash
python -m pytest tests/ -x -q
```

### Scenario matrix

| # | Given a model row whose telemetry carries… | Expect | Proves |
|---|---|---|---|
| 1 | canonical names for all three classes | all three class values rendered | SC-001, SC-002 |
| 2 | only the currently-emitted alias spellings (`gen_ai.usage.cache_creation.input_tokens`, `gen_ai.usage.reasoning_tokens`) | same values as scenario 1 | SC-006, [research.md D2](./research.md) |
| 3 | both a canonical and a legacy alias for one class, each carrying `100` | the class renders `100`, never `200` — first accepted name in declared order wins, aliases are never summed | FR-009, SC-008, [research.md D2](./research.md) |
| 4 | only `gen_ai.usage.input_tokens` / `output_tokens` (Foundry-native) | all three classes render `Not reported`; coverage `not_reported` | SC-003, [research.md D4](./research.md) |
| 5 | a class attribute whose value is `0` | renders `0`, **not** `Not reported` | SC-003 / FR-007 |
| 6 | one class present, two absent | per-row partial indicator shown **and** coverage `partial` | SC-005 / FR-022, Q3=B |
| 7 | three eligible unmapped `gen_ai.usage.*` attributes | all three retained verbatim, no truncation flag | SC-009 / FR-004 |
| 8 | seven eligible unmapped attributes | first five by ascending name retained, truncation flag set | SC-009 / FR-021 |
| 9 | a `gen_ai.usage.*` attribute with a negative or non-numeric value | discarded, absent from the row | FR-004, [research.md D1](./research.md) |
| 10 | an `llm.token_count.*` attribute | not admitted — outside the eligible group | Q1=B, [research.md D1](./research.md) |
| 11 | a source-level query failure **and** a partial class inventory | coverage state comes from the failure arm, with the failure `next_action` | [research.md D7](./research.md) |

### Non-regression checks

| # | Check | Proves |
|---|---|---|
| 12 | `build_agents_query` and `build_usage_query` emit unchanged text | Out of Scope |
| 13 | the agents view's `token_usage` coverage entry is unchanged | Out of Scope |
| 14 | `token_reporting_state` keeps its two-state behavior and its existing test passes untouched | [research.md D7](./research.md) |
| 15 | `input_tokens` / `output_tokens` render exactly as before | SC-004 / FR-015 |
| 16 | the literal `(observed usage, not billing data)` still appears verbatim | FR-016 |
| 17 | the Python and JavaScript renderers produce equivalent output for the same payload | FR-014, FR-022 |

### Wording gate

No artifact, UI string, or coverage text may contain monetary, cost, price, rate, spend, charge,
or billing language (FR-017). Quick check across the touched source files:

```bash
python -m pytest tests/unit/test_observe_ui.py -q -k "billing or disclaimer"
```

```powershell
Select-String -Path src\agentops\agent\observe\*.py -Pattern '(?i)\b(cost|price|pricing|billing|billable|spend|charge|rate card)\b'
```

The only permitted match is the existing `(observed usage, not billing data)` label.

---

## Live cockpit validation (requires a workspace with telemetry)

### Setup

```bash
az login
agentops cockpit
```

Open the models view for a project that has recent telemetry.

### What to confirm

| # | Step | Expected |
|---|---|---|
| 18 | Inspect a Foundry-native workload | Granular columns read `Not reported`; the coverage panel shows a `token_usage` entry whose `next_action` points at instrumentation, not at access or permissions |
| 19 | Inspect a workload instrumented with OpenLLMetry against Anthropic | cache-write reports a value while other classes may not; the per-row partial indicator appears |
| 20 | Inspect a workload instrumented with OpenLLMetry against OpenAI | reasoning reports a value under a different source attribute name than scenario 19 uses for its class |
| 21 | Compare the agents view and the combined usage view | Token rendering is visually identical to before this feature |
| 22 | Refresh the page | Server-rendered and client-rendered tables agree; no cell changes value or formatting on refresh |

Scenarios 19 and 20 together are the live counterpart of SC-006: two distinct vendor ecosystems
reporting granular classes under different source attribute names, normalized into the same
view without any vendor name appearing in the logic.

### If nothing granular appears

That is the expected result for a purely Foundry-hosted workload — Azure's instrumentation emits
only input and output token counts ([research.md D4](./research.md)). It is **not** a defect and
must not be reported as one. To see granular classes, point the cockpit at a project whose
callers are instrumented with a library that emits `gen_ai.usage.*` cache or reasoning
attributes.

---

## Exit criteria

The feature is validated when:

- [ ] All offline scenarios (1–17) pass
- [ ] The wording gate reports no new matches
- [ ] `python -m pytest tests/ -x -q` passes
- [ ] At least one live scenario from 18–20 has been observed, or its absence explained by
      [research.md D4](./research.md)
- [ ] Scenarios 21–22 show no visual change outside the models view's new columns and indicators
