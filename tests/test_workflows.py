from pathlib import Path

import yaml


def _workflow(name: str) -> dict:
    return yaml.load(
        Path(f".github/workflows/{name}.yml").read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


def test_update_workflow_is_daily_manual_and_serialized() -> None:
    workflow = _workflow("update")

    assert "schedule" in workflow["on"]
    assert "workflow_dispatch" in workflow["on"]
    assert workflow["permissions"]["contents"] == "write"
    assert workflow["concurrency"]["cancel-in-progress"] == "false"
    validation_steps = [
        step for step in workflow["jobs"]["update"]["steps"]
        if step.get("name") == "校验已发布订阅"
    ]
    assert "grep -q" in validation_steps[0]["run"]


def test_discovery_workflow_is_weekly_and_uses_github_token() -> None:
    workflow = _workflow("discover")
    steps = workflow["jobs"]["discover"]["steps"]

    assert "schedule" in workflow["on"]
    assert "workflow_dispatch" in workflow["on"]
    assert any("GITHUB_TOKEN" in str(step.get("env", {})) for step in steps)


def test_pages_workflow_has_minimum_deployment_permissions() -> None:
    workflow = _workflow("pages")

    assert workflow["permissions"]["pages"] == "write"
    assert workflow["permissions"]["id-token"] == "write"
    assert "deploy" in workflow["jobs"]
