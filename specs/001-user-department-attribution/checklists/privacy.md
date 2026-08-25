# Privacy Requirements Checklist: User and Department Attribution

**Purpose**: Formal pre-implementation gate for the completeness, clarity, consistency, and measurability of privacy, authorization, and identity-data-boundary requirements
**Created**: 2026-08-25
**Feature**: [spec.md](../spec.md)

**Note**: This checklist evaluates the quality of the written requirements, not implementation behavior.

**Review completed**: 2026-08-25. Gaps were resolved in
[spec.md](../spec.md) through FR-030–FR-046 and the strengthened SC-004,
SC-007, SC-009, SC-010, and assumptions. Traceability by group:

- CHK001–CHK006: FR-030, FR-031, FR-035, FR-045
- CHK007–CHK012: FR-031–FR-033, FR-036
- CHK013–CHK017: FR-030, FR-032–FR-034, FR-038, FR-045
- CHK018–CHK021: FR-030, FR-043, FR-046, SC-004, SC-007, SC-009, SC-010
- CHK022–CHK026: FR-032–FR-037, FR-041, FR-045
- CHK027–CHK031: FR-031, FR-033, FR-034, FR-039, SC-009
- CHK032–CHK035: FR-037, FR-038, FR-040, FR-044
- CHK036–CHK040: FR-030, FR-034, FR-038, FR-041, FR-042

## Requirement Completeness

- [x] CHK001 Are all privacy-sensitive data classes explicitly identified—raw identity, pseudonymous user keys, group IDs and claims, mapping configuration, filter tokens, department labels, and attribution counts—with a lifecycle and permitted surfaces for each? [Completeness, Gap, Spec §Affected Product Contracts]
- [x] CHK002 Are the eligible authenticated identity representations and every prohibited fallback category exhaustively documented, including the treatment of empty and conflicting aliases? [Completeness, Spec §FR-004, §FR-027, §FR-028]
- [x] CHK003 Are data-boundary requirements defined for source telemetry, aggregate and delegated query results, HTTP responses, URLs, browser state, shared caches, diagnostics, errors, deployment previews and journals, Doctor output, and release evidence? [Completeness, Gap, Spec §FR-019]
- [x] CHK004 Are authorization requirements specified for every individual-level form: a user list, user comparison, explicit user filter, department filter that resolves to one person, and mapping-bootstrap view? [Completeness, Gap, Spec §FR-017–FR-020]
- [x] CHK005 Is the privacy-preserving mapping-bootstrap workflow fully required, including empty initial mappings, delegated identity-to-key visibility, operator mapping updates, and renewed deployment confirmation? [Completeness, Gap, Spec §FR-003, §FR-006]
- [x] CHK006 Are enablement, disablement, declined confirmation, invalid configuration, and explicit rotation requirements complete enough to define the resulting privacy and authorization state without relying on implementation assumptions? [Completeness, Spec §FR-001–FR-003, Edge Cases]

## Requirement Clarity

- [x] CHK007 Are “authenticated,” “stable,” and “documented runtime-specific alias” defined with objective eligibility rules rather than left to runtime-name or operator interpretation? [Clarity, Ambiguity, Spec §FR-004]
- [x] CHK008 Is exact comparison behavior specified when both eligible aliases are present, including whitespace normalization and whether case normalization is prohibited? [Clarity, Gap, Spec §FR-004, Edge Cases]
- [x] CHK009 Is “current delegated response” bounded precisely enough to determine whether raw identity may appear in a selected-user response, a bounded multi-user bootstrap response, or both? [Clarity, Ambiguity, Spec §FR-019, Assumptions]
- [x] CHK010 Is “individual-level view” objectively classified for lists, comparisons, filters, singleton departments, and identity-bearing coverage or diagnostic counts? [Clarity, Spec §FR-017, §FR-020]
- [x] CHK011 Is the safe aggregate criterion “does not reveal or narrow to one person” quantified by the selected scope, time range, filters, active-user cardinality, and zero/no-data semantics? [Clarity, Ambiguity, Spec §FR-020, Edge Cases]
- [x] CHK012 Is “actionable message” constrained to remain useful without revealing whether a person, mapping, token subject, or protected resource exists? [Clarity, Gap, Spec §FR-015, §FR-019]

## Requirement Consistency

- [x] CHK013 Are the requirements permitting persisted group IDs in department mappings consistent with the statement that raw group identifiers remain protected, including where such IDs may and may not be disclosed? [Consistency, Spec §FR-006, Assumptions]
- [x] CHK014 Are opaque URL-filter requirements consistent with the classification of pseudonymous keys and tokens as linkable personal data and with the prohibition on raw identity in browser storage? [Consistency, Spec §FR-016, §FR-019, Affected Product Contracts]
- [x] CHK015 Are aggregate department access, singleton escalation, delegated-only individual access, and shared-cache prohibition expressed as one non-conflicting access-boundary rule? [Consistency, Spec §FR-017–FR-020]
- [x] CHK016 Is the promise not to change Cockpit authorization consistent with widened delegated data handling and the operator permissions required to query individual Azure Monitor evidence? [Consistency, Spec §FR-003, §FR-018, §FR-025]
- [x] CHK017 Are disabled-state parity and configuration-removal reversal requirements consistent across usage, cost, coverage, filtering, URLs, deployment settings, and authorization? [Consistency, Spec §FR-002, §FR-003, User Story 3 Scenario 4]

## Acceptance Criteria Quality

- [x] CHK018 Does the 100% privacy criterion enumerate every identity-bearing surface that must exclude raw identity or linkable values, rather than limiting measurement to URLs, persistent state, and application logs? [Acceptance Criteria, Gap, Spec §SC-004]
- [x] CHK019 Is the zero-new-privilege outcome measurable against a defined baseline and an explicit inventory of cloud roles, delegated permissions, directory permissions, and deployment-identity capabilities? [Measurability, Spec §SC-007, §FR-024]
- [x] CHK020 Is pseudonym stability and separation objectively measurable through specified same-key and different-key inputs for restart, version deployment, tenant, deployment namespace, generation, and explicit rotation? [Measurability, Spec §SC-009, §FR-006]
- [x] CHK021 Are measurable outcomes defined for each privacy failure path—missing delegated access, stale or foreign filters, claim overage, ambiguous identity, invalid configuration, and partial source failure? [Acceptance Criteria, Gap, Spec §SC-003, §SC-004]

## Scenario Coverage

- [x] CHK022 Does the primary department-attribution scenario state all prerequisites for aggregate access to remain privacy-safe before exposing grouped usage or cost? [Coverage, Spec §User Story 1, §FR-020]
- [x] CHK023 Does the protected-user scenario cover both investigation and mapping bootstrap, including whether a multi-user list and comparisons are permitted under the same delegated boundary? [Coverage, Gap, Spec §User Story 2, §FR-017]
- [x] CHK024 Are alternate mapping paths complete for explicit user mapping, exact current-principal group mapping, no mapping, unavailable claims, and multiple applicable departments? [Coverage, Spec §FR-006–FR-009]
- [x] CHK025 Are exception requirements complete and local for invalid configuration, unavailable OBO credentials, malformed or unauthorized filters, unreadable sources, and absent eligible identity without producing a broader or success-shaped result? [Coverage, Exception Flow, Spec §FR-015, §FR-018, §FR-021–FR-023]
- [x] CHK026 Are recovery requirements defined for disabling attribution, correcting invalid configuration, rotating pseudonyms, rebuilding mappings, and handling old URLs without retaining a previous-generation grace path? [Coverage, Recovery Flow, Spec §FR-003, §FR-006, §FR-016, Edge Cases]

## Edge Case Coverage

- [x] CHK027 Are requirements deterministic for empty aliases, equal aliases, conflicting aliases, casing differences, surrounding whitespace, and identity values that differ only by normalization? [Edge Case, Gap, Spec §FR-004]
- [x] CHK028 Are cross-tenant, cross-scope, cross-deployment, and cross-generation correlation boundaries independently specified rather than grouped under the vague term “deployment-scoped”? [Edge Case, Spec §FR-006, §FR-014, §SC-009]
- [x] CHK029 Are singleton rules defined after all applicable scope, time, usage/cost, department, and user filters, including departments with zero activity and privacy-sensitive coverage counts? [Edge Case, Gap, Spec §FR-017, §FR-020]
- [x] CHK030 Are group-claim overage, missing claims, duplicate configured group IDs, and membership in groups mapped to different departments addressed without permitting directory lookup or silent selection? [Edge Case, Spec §FR-005, §FR-007–FR-009, Edge Cases]
- [x] CHK031 Are token invalidation requirements differentiated for semantic mapping changes, configuration reordering, scope changes, principal changes, malformed input, and explicit rotation? [Edge Case, Gap, Spec §FR-014–FR-016]

## Non-Functional Privacy and Security Requirements

- [x] CHK032 Does the specification explicitly state that pseudonymous keys remain linkable personal data rather than anonymous data, including the privacy limits of deterministic hashing for low-entropy identities? [Security, Privacy, Gap, Spec §Attribution Configuration]
- [x] CHK033 Are fail-closed requirements preserved under timeout, partial failure, cache failure, and delegated-token failure so non-functional degradation cannot silently change the credential or privacy boundary? [Security, Resilience, Gap, Spec §FR-015, §FR-018, §FR-023]
- [x] CHK034 Are non-cacheability requirements defined for application caches, intermediary/shared caches, browser caching, and response retention, including the exact policy expected for every delegated or singleton response? [Security, Completeness, Gap, Spec §FR-019]
- [x] CHK035 Are privacy-preserving observability requirements defined so maintainers can diagnose attribution failures without recording raw identities, mappings, group IDs, user rows, or opaque filter tokens? [Security, Operability, Gap, Spec §FR-019, §FR-022]

## Dependencies and Assumptions

- [x] CHK036 Are the existing Easy Auth, OBO, delegated Azure Monitor permission, direct log-RBAC, tenant-validation, and Observe-scope assumptions explicitly documented with the consequences when any assumption is false? [Dependency, Assumption, Spec §FR-018, Assumptions]
- [x] CHK037 Are the authoritative Azure Monitor semantics and limitations for `UserAuthenticatedId` and `enduser.id` documented as requirements dependencies, including runtimes that emit only anonymous identity? [Dependency, Assumption, Spec §FR-004, §FR-027]
- [x] CHK038 Are ownership and access assumptions for reading or changing the privacy-sensitive App Service/azd mapping configuration defined without introducing a new per-user permission model? [Dependency, Gap, Spec §FR-003, §FR-025, §FR-026]

## Ambiguities and Conflicts

- [x] CHK039 Is the apparent breadth of “known group identifiers” for cross-user department grouping reconciled explicitly with the prohibition on using group claims to classify any telemetry user other than the signed-in principal? [Conflict, Spec §FR-006, §FR-007]
- [x] CHK040 Is the boundary between transient response data and prohibited runtime/browser persistence precise enough to cover server memory, browser DOM/history, copied URLs, refresh behavior, and navigation away from a protected view? [Ambiguity, Gap, Spec §FR-016, §FR-019]

## Notes

- Check items off as the requirements are reviewed and clarified.
- Record findings or required specification edits inline.
- A checked item means the written requirement is complete and reviewable; it does not certify implementation behavior.
