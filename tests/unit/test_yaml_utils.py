from agentops.utils.yaml import save_yaml


def test_save_yaml_keeps_long_url_scalar_inline(tmp_path):
    config_path = tmp_path / "agentops.yaml"
    agent = (
        "https://ca-4oa7xxpgqecaa-orchestrator.bravewater-73902d56."
        "uksouth.azurecontainerapps.io/orchestrator"
    )

    save_yaml(config_path, {"version": 1, "agent": agent})

    assert f"agent: {agent}" in config_path.read_text(encoding="utf-8")
