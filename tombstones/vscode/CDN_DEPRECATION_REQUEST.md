# VS Code Marketplace CDN Deprecation Request

After the tombstone VSIX (`AgentOpsToolkit.agentops-toolkit` v0.3.0) is published and live on the Marketplace, file a deprecation request with the VS Code Marketplace team so the legacy listing is officially marked as deprecated and points to the new extension.

File the request as a new discussion in the VS Code Discussions repo (use the most current "Marketplace deprecation" thread if one already exists, otherwise open a new discussion):

- <https://github.com/microsoft/vscode-discussions/discussions/1>

Copy-paste the block below into the discussion, filling in the publisher contact details:

> **Subject:** Mark `AgentOpsToolkit.agentops-toolkit` as deprecated; point users to `AgentOpsAccelerator.agentops-accelerator`
>
> **Legacy extension ID:** `AgentOpsToolkit.agentops-toolkit`
> **New extension ID:** `AgentOpsAccelerator.agentops-accelerator`
> **Reason:** Publisher rename. The project rebranded from "AgentOps Toolkit" to "AgentOps Accelerator". Because the Marketplace cannot rename a publisher in place, the extension was republished under a new publisher identifier. A final v0.3.0 tombstone build of the legacy extension has been shipped that prompts existing installs to migrate.
> **Requested actions on the legacy listing (`AgentOpsToolkit.agentops-toolkit`):**
>
> 1. Mark the extension as **deprecated** in the Marketplace UI.
> 2. Add a **migrate-to** pointer to `AgentOpsAccelerator.agentops-accelerator` so the Marketplace surfaces the replacement extension.
> 3. (Optional) Hide the legacy listing from search results once the migrate-to pointer is in place.
>
> **Tracking issue:** <https://github.com/Azure/agentops/issues/181>
>
> **Publisher contact:** _<add Marketplace publisher email / Azure DevOps org admin here before sending>_

## Pre-flight checklist

- [ ] Tombstone VSIX (`AgentOpsToolkit.agentops-toolkit` v0.3.0) is published to the Marketplace and the listing is live.
- [ ] New extension (`AgentOpsAccelerator.agentops-accelerator`) is published to the Marketplace and the listing is live.
- [ ] Tracking issue <https://github.com/Azure/agentops/issues/181> is up to date with the publish status of both extensions.
- [ ] Publisher contact (email / Azure DevOps org admin) is filled in above before submitting.
