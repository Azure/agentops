# CLI Contract: Hosted Cockpit

## Compatibility

`agentops cockpit` remains the local Cockpit command with the existing options:

```text
agentops cockpit [--host HOST] [--port PORT] [--workspace PATH]
                 [--no-preflight]
```

The implementation uses a Typer group callback so adding the subcommand does not
change no-argument behavior, output, browser launch, or port handling.

## Deploy command

```text
agentops cockpit deploy [--workspace PATH]
                        [--subscription ID]
                        [--resource-group NAME]
                        [--location REGION]
                        [--scope projects|foundry|resource-group|subscription]
                        [--project-id ARM_ID ...]
                        [--scope-resource-id ARM_ID]
                        [--tenant-id ID]
                        [--client-id ID]
                        [--allowed-group ID]
                        [--name NAME]
                        [--preview]
                        [--yes]
```

### Defaults

- `--workspace`: current directory.
- `--scope`: `projects`, containing the one project resolved from the current
  workspace.
- `--subscription`, `--resource-group`, and `--location`: current azd/Azure
  context when unambiguous; otherwise prompt.
- `--name`: deterministic from workspace/project plus a collision-safe suffix.
- No allowed group unless explicitly selected.

### Interactive behavior

1. Resolve and display the current project and linked telemetry.
2. Allow explicit scope expansion.
3. Validate Azure, azd, app registration, consent, group, and deployer rights.
4. Display the complete infrastructure/federation/RBAC preview.
5. Require confirmation before mutation.

`--preview` stops after step 4 with exit code `0`.

`--yes` suppresses the final confirmation only when every required deployment
and scope value was provided explicitly. It does not bypass validation or
subscription-wide warnings.

### Output

Successful deployment prints:

- effective Observe scope;
- Web App resource ID;
- UAMI resource ID;
- Cockpit HTTPS URL;
- Azure portal resource URL;
- health status and any bounded RBAC propagation warning.

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Preview or deployment completed successfully. |
| `1` | Invalid configuration, failed validation, denied permission, azd/Bicep failure, federation conflict, deploy failure, or failed health verification. |
| `2` | Reserved by the global AgentOps threshold contract; not emitted by Cockpit deployment. |

### Safety requirements

- No Azure mutation occurs before the preview and confirmation.
- The command never creates an app registration or service principal.
- Rerun reuses exact resources, role assignments, and federated credentials.
- The running hosted application exposes no deployment command or mutation API.
