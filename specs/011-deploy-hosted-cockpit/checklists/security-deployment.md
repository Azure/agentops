# Security and Deployment Requirements Checklist: Deploy Hosted Cockpit

**Purpose**: Formal pre-task gate for the completeness, clarity, consistency, and measurability of security, identity, deployment, and recovery requirements.
**Created**: 2026-08-20
**Feature**: [spec.md](../spec.md)

**Note**: This checklist evaluates the quality of the written requirements, not the implementation. All blocking gaps, especially recovery and rollback gaps, must be resolved before `/speckit-tasks`.

## Requirement Completeness

- [x] CHK001 Are the exact Azure resources that deployment may create, modify, reuse, and never touch completely enumerated in the normative requirements rather than only in the plan? [Completeness, Spec §FR-054, Constitution §IV]
- [x] CHK002 Are the minimum Azure RBAC and Microsoft Graph permissions required from the deployer specified per deployment stage and target resource? [Resolved, Spec §FR-055]
- [x] CHK003 Are the runtime permissions for the UAMI specified as exact role names, assignment scopes, and prohibited roles? [Completeness, Spec §FR-056, §FR-064]
- [x] CHK004 Are authentication requirements defined for every hosted route, including the anonymous health endpoint and protected API endpoints? [Resolved, Spec §FR-057]
- [x] CHK005 Are all prerequisites for the existing single-tenant app registration documented, including redirect URI, supported account type, delegated consent, token audience, and federation? [Completeness, Spec §FR-058]
- [x] CHK006 Are requirements defined for creating, reusing, conflicting with, rotating, and intentionally replacing the federated credential? [Coverage, Spec §FR-059]
- [x] CHK007 Are the security properties of every persisted application setting identified, including which values are non-secret and which values are prohibited? [Completeness, Spec §FR-060]
- [x] CHK008 Are tenant-only and optional group-restricted access requirements complete for sign-in, token validation, group resolution, and group-claim overage? [Resolved, Spec §FR-057, §FR-061, §FR-072]

## Requirement Clarity

- [x] CHK009 Is “required deployer permissions” expressed as an explicit permission matrix rather than an implementation-dependent phrase? [Resolved, Spec §FR-055]
- [x] CHK010 Is the boundary between deployment-time mutation and runtime read-only behavior unambiguous for every component and identity? [Clarity, Spec §FR-054, §FR-065, Constitution §IV]
- [x] CHK011 Is “read-only” defined in terms of permitted Azure data-plane and control-plane operations, including whether query submission and token acquisition are considered reads? [Resolved, Spec §FR-065]
- [x] CHK012 Is the canonical identity of the existing app registration distinguished from its client ID, object ID, service principal ID, and tenant ID wherever each is required? [Clarity, Spec §FR-058, §FR-063]
- [x] CHK013 Is the relationship among configured Observe scope, discovery constraints, Reader scope, and derived Log Analytics Reader assignments explicit for every scope mode? [Clarity, Spec §FR-064]
- [x] CHK014 Is “actionable remediation” defined with minimum required fields such as failed prerequisite, affected stage, safe retry guidance, and required operator action? [Resolved, Spec §FR-062]

## Requirement Consistency

- [x] CHK015 Do the requirements consistently prohibit tenant-identity creation while permitting only the explicitly approved federated-credential mutation on an existing app registration? [Consistency, Spec §FR-005, §FR-054, §FR-059, Constitution §IV]
- [x] CHK016 Do the scope-expansion requirements align with the rule that RBAC is authoritative and configuration alone cannot grant access? [Consistency, Spec §FR-004A, §FR-016D–§FR-016F, §FR-064]
- [x] CHK017 Are the shared UAMI access model and per-user OBO access model consistently separated across authentication, authorization, caching, and disclosure requirements? [Consistency, Spec §FR-056, §FR-057, §FR-072, §FR-073]
- [x] CHK018 Do rerun requirements consistently preserve the UAMI, federated trust, resource IDs, role assignments, and application URL unless the operator explicitly changes configuration? [Consistency, Spec §FR-008, §FR-010B–§FR-010D, §FR-012C, §SC-008]
- [x] CHK019 Do CLI exit-code requirements remain consistent with the global AgentOps contract for preview success, deployment success, configuration failure, and readiness threshold failure? [Consistency, Spec §FR-066, Constitution §I]

## Acceptance Criteria Quality

- [x] CHK020 Can least-privilege compliance be objectively assessed from criteria that enumerate every allowed role, scope, identity, and prohibited write capability? [Measurability, Spec §FR-054–§FR-056, §FR-064–§FR-065, §SC-009]
- [x] CHK021 Can authentication-boundary compliance be objectively assessed for unauthenticated, wrong-tenant, disallowed-group, and allowed-user scenarios? [Measurability, Spec §FR-057, §FR-061, §FR-072, §SC-002]
- [x] CHK022 Can secretless deployment be objectively assessed across application settings, deployment artifacts, logs, command output, and runtime configuration? [Resolved, Spec §FR-060, §SC-009]
- [x] CHK023 Can idempotency be objectively assessed for resources, RBAC assignments, federated credentials, configuration, and stable URLs after both successful and partially failed deployments? [Measurability, Spec §FR-008, §FR-010A–§FR-010F, §FR-059, §SC-008]
- [x] CHK024 Are health-verification success and failure criteria specific enough to distinguish liveness, authenticated application readiness, configuration validity, and effective UAMI access? [Clarity, Spec §FR-071]

## Deployment Scenario Coverage

- [x] CHK025 Are primary-flow requirements complete from workspace resolution through prerequisite validation, preview, confirmation, provisioning, federation, deployment, and health verification? [Coverage, Spec §FR-002–§FR-010, §FR-055, §FR-070–§FR-071]
- [x] CHK026 Are non-interactive deployment requirements explicit about mandatory inputs, confirmation suppression, warnings that cannot be bypassed, and validation that remains mandatory? [Resolved, Spec §FR-067]
- [x] CHK027 Are requirements defined for ambiguous workspace project resolution, zero matching projects, multiple matching projects, and explicit project selection? [Coverage, Spec §FR-003, §FR-003B, §FR-068]
- [x] CHK028 Are requirements defined for denied ARM role-assignment permissions, denied Graph federation permissions, invalid consent, unresolved groups, and delayed RBAC propagation? [Coverage, Spec §FR-069]
- [x] CHK029 Are requirements defined for a preview that reports unknown changes, destructive changes, out-of-bound changes, or drift from the last deployment? [Resolved, Spec §FR-070]

## Recovery and Rollback Gate

- [x] CHK030 **BLOCKING** Does the spec define which completed mutations must be rolled back when each later deployment stage fails? [Recovery, Spec §FR-010B–§FR-010C]
- [x] CHK031 **BLOCKING** Does the spec define when automatic rollback is required, when preserving successfully created resources is safer, and when operator confirmation is required before cleanup? [Recovery, Spec §FR-010B–§FR-010C]
- [x] CHK032 **BLOCKING** Are rollback boundaries explicit for Bicep-provisioned resources, role assignments, app settings, auth settings, and the federated credential? [Recovery, Spec §FR-010B–§FR-010C, Constitution §IV]
- [x] CHK033 **BLOCKING** Are retry and resume requirements defined for failures before confirmation, during provisioning, during federation, during application deployment, during health verification, and during RBAC propagation? [Recovery, Spec §FR-010D, §FR-010F]
- [x] CHK034 **BLOCKING** Is the durable deployment state required to distinguish resources created by this attempt from pre-existing resources that must never be removed during rollback? [Recovery, Spec §FR-010A, Data Model §DeploymentJournal]
- [x] CHK035 **BLOCKING** Are requirements defined for rerunning after partial deployment without duplicating resources, widening permissions, replacing the stable UAMI, or masking unresolved failures? [Coverage, Recovery, Spec §FR-008, §FR-010D, §FR-012C]
- [x] CHK036 **BLOCKING** Does the failure contract require reporting completed mutations, pending mutations, rollback actions, rollback failures, safe retry steps, and the resulting application usability state? [Recovery, Spec §FR-010E]

## Security Edge Cases and Dependencies

- [x] CHK037 Are requirements defined for token expiry, missing Easy Auth token headers, invalid audience, tenant mismatch, and unavailable downstream token exchange? [Resolved, Spec §FR-072]
- [x] CHK038 Are requirements defined for protected-table queries that succeed with zero rows despite denied privileged access, so absence is not misclassified as “no data”? [Coverage, Spec §FR-073]
- [x] CHK039 Are requirements explicit that raw generative-AI content cannot enter shared caches, URLs, browser persistence, telemetry, diagnostics, or deployment logs? [Completeness, Spec §FR-060, §FR-073, §SC-009A]
- [x] CHK040 Are public-preview dependencies for `AppGenAIContent`, protected tables, and managed-identity federation documented with release-time revalidation and a defined unsupported-state outcome? [Dependency, Spec §FR-074]
- [x] CHK041 Are sovereign-cloud or non-public-cloud behaviors explicitly supported or excluded for issuer, audience, portal links, and Azure API endpoints? [Resolved, Spec §FR-075, Scope Boundaries]
- [x] CHK042 Are requirements defined for authorization drift after deployment, including revoked UAMI roles, changed group membership, removed consent, replaced federation, and narrowed user permissions? [Resolved, Spec §FR-076]

## Governance and Traceability

- [x] CHK043 Does every planned mutation map to an explicit functional requirement and constitutional allowance, with no mutation justified only by implementation detail? [Traceability, Spec §FR-054–§FR-065, Constitution §IV]
- [x] CHK044 Are security ownership and approval requirements specified for app-registration changes, Graph consent, role assignments, scope expansion, and rollback decisions? [Resolved, Spec §FR-077]
- [x] CHK045 Does the documentation requirement cover deployment prerequisites, identity boundaries, effective shared scope, per-user protected access, failure recovery, rollback, and safe rerun guidance? [Completeness, Spec §FR-051–§FR-051A]
- [x] CHK046 Are all assumptions that can block secure deployment assigned a validation point and an explicit failure outcome before mutation begins? [Resolved, Spec §FR-078]
- [x] CHK047 Does the spec require the public **Operate** page to distinguish `AppGenAIContent` routing, feature enablement, table protection, standard and privileged access, OBO, the UAMI boundary, migration dates, and zero-row denial behavior while linking to the detailed deployment guide? [Completeness, Spec §FR-051A, §FR-074]

## Notes

- Check items off as completed: `[x]`.
- Record findings and requirement changes inline.
- Any unresolved `BLOCKING` item prevents task generation.
- This gate is intended for maintainer or security reviewer approval before implementation tasks are generated.
- Review completed on 2026-08-21. FR-054 through FR-078 close the
  permission, route authorization, identity, failure, preview, cloud-support,
  drift, governance, and validation gaps identified by this checklist.
