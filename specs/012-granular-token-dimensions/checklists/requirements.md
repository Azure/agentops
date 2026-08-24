# Specification Quality Checklist: Granular Token Dimensions in the Models View

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-23
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`

### Resolution

All 16 items pass. The three `[NEEDS CLARIFICATION]` markers that originally blocked
this checklist were resolved in **Session 2026-08-23** and are recorded in the
`## Clarifications` section of the spec:

- **FR-002** — the vocabulary is fixed at exactly three classes: cache-read,
  cache-write, and reasoning, alongside the existing input and output totals.
- **FR-003** — long-context consumption is excluded as both a token class and a
  request-level classification, because it is a rate tier over tokens already counted
  in the input total, no source reports it as a distinct count, and thresholding it
  would require owning a rate-tier boundary the feature places out of scope.
- **FR-004** — vendor-specific classes outside the vocabulary are retained
  unnormalized under their source attribute names, so nothing observed is discarded.

Consequential updates made at the same time so the requirement set stays internally
consistent: FR-010 now names the passthrough as the destination for unrecognized
attributes rather than reading as a bare exclusion; User Story 3 gains an acceptance
scenario covering an unrecognized attribute surfacing through the passthrough and one
confirming it does not perturb the coverage state; SC-009 makes the no-loss guarantee
measurable; and the Edge Cases, Out of Scope, Key Entities, and Assumptions sections
each carry the long-context and passthrough decisions.

Every functional requirement now has matching acceptance criteria in User Stories 1
through 3 or a corresponding success criterion.

**Session 2026-08-24** resolved four residual ambiguities without regressing any
item. All 16 remain passing:

- **FR-004** gains a passthrough eligibility rule — same attribute group as the
  existing token counts, non-negative numeric value — closing an untestable gap.
- **FR-021** bounds unnormalized retention at five per record with a truncation
  indication. SC-009 was reworded because its previous unqualified "zero observed
  token values discarded" claim contradicted a truncating bound; SC-010 makes the
  bound measurable and Edge Cases covers the overflow case.
- **FR-022** requires a per-row partial-coverage indication in the models view,
  verified by User Story 2 scenario 6 and SC-011.
- **FR-020** and SC-006 now state the family selection rule rather than leaving the
  choice open, so the mapping verification is guaranteed to exercise real naming
  divergence across vendors.
