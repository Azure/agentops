# AgentOps What's new

Explore major updates by month, or read the [full GitHub release notes](https://github.com/Azure/agentops/releases) for all changes and fixes.

## October 2026

| Release | Highlights |
| --- | --- |
| [v0.16.0](https://github.com/Azure/agentops/releases/tag/v0.16.0) | Review recorded evaluation-checkout commits and tracked configuration changes alongside regressions in [reports and Doctor](how-it-works.md#regression-commit-attribution), and browse evaluated-version history in [Cockpit](operate.md#cockpit). These comparisons do not prove a regression's cause or verify the deployed source. |

## September 2026

| Release | Highlights |
| --- | --- |
| [v0.15.0](https://github.com/Azure/agentops/releases/tag/v0.15.0) | Added recipe-based [azd evaluation integration](evaluation.md#delegating-to-azd) for `azd ai eval`, preserving AgentOps results and threshold gates without changing existing `azd ai agent eval` recipes. The new path requires the preview `azure.ai.evaluations` extension, which was not yet publicly available at release. |
| [v0.14.0](https://github.com/Azure/agentops/releases/tag/v0.14.0) | Removed hosted Cockpit, `agentops cockpit deploy`, the Observe UI and its APIs. Use the [local, read-only Cockpit](operate.md#cockpit) for readiness and evaluation history, and [Foundry or Azure Monitor](foundry-ops-workbook.md) for runtime observability. |

## August 2026

| Release | Highlights |
| --- | --- |
| [v0.10.0](https://github.com/Azure/agentops/releases/tag/v0.10.0) | Read evaluation datasets directly from private [Azure Blob Storage or ADLS Gen2](azure-storage-datasets.md) using your existing Azure identity instead of managing local copies. Objects are limited to 100 MiB and require data-reader permissions and network access, with no SAS or account-key support. |

## July 2026

| Release | Highlights |
| --- | --- |
| [v0.8.0](https://github.com/Azure/agentops/releases/tag/v0.8.0) | Inspect evaluator pass rates, raw scores and failed evaluation events in the Agent behavior tab of the [Foundry operations workbook](foundry-ops-workbook.md). The tab reads compatible Application Insights telemetry and shows missing-data diagnostics, but does not instrument agents or schedule evaluations. |

## June 2026

| Release | Highlights |
| --- | --- |
| [v0.6.0](https://github.com/Azure/agentops/releases/tag/v0.6.0) | Turn production traces from Azure Monitor or Application Insights into reviewable evaluation datasets with [telemetry imports](evaluation.md#import-production-traces-into-a-dataset). Preview rows before writing them and review labels before gating a release, since self-similarity detects drift rather than proving correctness. |
