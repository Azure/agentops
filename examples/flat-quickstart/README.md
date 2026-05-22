# Flat quickstart example

The smallest possible AgentOps 1.0 setup: an `agentops.yaml`, a JSONL dataset, and one CLI command.

## Run

```bash
cd examples/flat-quickstart
export AZURE_AI_FOUNDRY_PROJECT_ENDPOINT="https://<resource>.services.ai.azure.com/api/projects/<project>"
agentops eval run --config agentops.yaml --output ./out
```

Edit `agentops.yaml` first to point `agent:` at one of your real targets (Foundry prompt agent, Foundry hosted endpoint, generic HTTP/JSON agent, or `model:<deployment>`).

Outputs land in `./out/results.json` and `./out/report.md`.
