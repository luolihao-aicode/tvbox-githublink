from pathlib import Path

import pytest
import yaml

from iptv_manager.config import load_config


def _write_yaml(path: Path, data: object) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")


def test_load_config_rejects_unknown_channel_category(tmp_path: Path) -> None:
    _write_yaml(tmp_path / "sources.yml", {"sources": []})
    _write_yaml(
        tmp_path / "channels.yml",
        {
            "channels": [
                {
                    "id": "movie-demo",
                    "name": "电影演示频道",
                    "category": "电影",
                    "aliases": [],
                }
            ]
        },
    )
    _write_yaml(tmp_path / "policy.yml", {})

    with pytest.raises(ValueError, match="频道分类"):
        load_config(tmp_path)


def test_load_config_rejects_unknown_top_level_key(tmp_path: Path) -> None:
    _write_yaml(tmp_path / "sources.yml", {"sources": [], "sourcez": []})
    _write_yaml(tmp_path / "channels.yml", {"channels": []})
    _write_yaml(tmp_path / "policy.yml", {})

    with pytest.raises(ValueError, match="未知字段"):
        load_config(tmp_path)
