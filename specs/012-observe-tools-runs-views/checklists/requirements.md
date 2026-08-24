# Specification Quality Checklist: Observe tools and runs views

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

### Validation iteration 1 (2026-08-23)

**Failing**: "No [NEEDS CLARIFICATION] markers remain" — 2 markers remain, both
awaiting a user decision. Neither can be resolved by a reasonable default:

1. **FR-018 — runtime value evolution.** The issue asks to refine the reported
   runtime from a coarse two-value set into a five-value set. Whether that
   replaces the existing values (breaking a published contract) or is reported
   alongside them is a governance decision with no safe default, and it
   directly conflicts with the project constitution's "preserve public
   contracts" principle unless explicitly decided.
2. **FR-009 — run boundary.** Whether a "run" is one correlated trace or a
   multi-turn session changes what turn count measures, how many rows an
   operator sees, and the duration figure that later cost allocation depends
   on. The issue itself lists this as an open question.

**Fixes applied in this iteration**:

- Reworded FR-018 to state the question in stakeholder terms rather than naming
  contract enum values, satisfying "No implementation details".
- Reworded the Cockpit-scope assumption to avoid describing the query surface,
  satisfying "Written for non-technical stakeholders".

**Deliberately resolved by informed guess rather than a marker** (documented in
Assumptions, per the 3-marker limit and the scope > security > UX > technical
priority order):

- Whether Foundry prompt agents emit runtime-identifying activity — resolved by
  FR-017 (report unknown, never infer).
- Whether Copilot Studio tool names normalize cleanly — resolved by FR-005
  (report through coverage, never synthesize a name).
- Result-set bounding and ordering — resolved by FR-028 and the ordering
  assumption, inherited from spec 011 FR-044.
- Run token-total completeness — resolved by FR-011 (absent, never zero).

### Validation iteration 2 (2026-08-23)

**Status**: All 16 items pass. Both open markers were resolved by user decision.

**Q1 — runtime value evolution → Option A (replace outright).** The refined
five-value runtime set replaces today's coarse Foundry and external values
rather than being reported alongside them, so a reported runtime always has one
granularity. Captured as FR-018 and FR-018A, with SC-012 and a matching edge
case and acceptance scenario.

> **Carry into planning**: this is a *declared, accepted* breaking change to a
> published contract and therefore an explicit exception to constitution
> Principle I ("Preserve Public Contracts", additive/backward-compatible schema
> evolution). Planning MUST record the exception, publish the old-to-new value
> mapping, and version the change. Note the mapping is not one-to-one: the old
> Foundry value splits into Foundry hosted and Foundry prompt, and the old
> external value splits into external registered and external unregistered, so
> some agents will move to unknown until their runtime is determinable.

**Q2 — run boundary → Option C (session when reported, else single trace).** A
run uses the most specific correlation available, keeping all five runtimes
representable including those that report no session identity. Captured as
FR-009 and FR-009A, with SC-011, a mixed-granularity edge case, and a fifth
acceptance scenario on User Story 2.

> **Carry into planning**: rows have mixed granularity by design. Every run row
> must state which correlation formed it (FR-009A), otherwise turn count and
> duration are not comparable across rows. This interacts with FR-012
> (incomplete runs at the time-range boundary) and with the run-duration-as-
> compute-allocation-key rationale behind SC-010.

**Consistency updates applied in this iteration**: User Story 2 Independent Test
and acceptance scenarios, User Story 4 acceptance scenarios, two new edge cases,
the Observed run key entity, SC-011 and SC-012, and two new Assumptions
recording both decisions and their costs.
