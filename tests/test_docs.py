from pathlib import Path


def test_readme_covers_operation_and_safety_topics() -> None:
    readme = Path("README.md").read_text(encoding="utf-8")

    for heading in (
        "本地运行",
        "GitHub Actions",
        "订阅地址",
        "检测指标",
        "授权与来源说明",
        "故障处理",
    ):
        assert heading in readme
    assert "不代表用户本地" in readme


def test_initial_generated_json_has_schema_version() -> None:
    import json

    for name in ("status.json", "report.json"):
        payload = json.loads(Path("output", name).read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
