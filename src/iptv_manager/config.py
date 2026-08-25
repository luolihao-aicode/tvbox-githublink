from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from .models import AppConfig, ChannelRule, GitHubSearchConfig, PolicyConfig, SourceConfig


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ValueError(f"缺少配置文件：{path.name}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"无法读取配置文件 {path.name}：{exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"配置文件 {path.name} 的顶层必须是对象")
    return data


def _reject_unknown_keys(data: dict[str, Any], allowed: set[str], filename: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"配置文件 {filename} 包含未知字段：{', '.join(unknown)}")


def load_config(config_dir: Path) -> AppConfig:
    sources_data = _read_yaml(config_dir / "sources.yml")
    channels_data = _read_yaml(config_dir / "channels.yml")
    policy_data = _read_yaml(config_dir / "policy.yml")
    _reject_unknown_keys(sources_data, {"sources", "github_search"}, "sources.yml")
    _reject_unknown_keys(channels_data, {"channels"}, "channels.yml")
    try:
        return AppConfig(
            sources=[SourceConfig.model_validate(item) for item in sources_data.get("sources", [])],
            channels=[ChannelRule.model_validate(item) for item in channels_data.get("channels", [])],
            policy=PolicyConfig.model_validate(policy_data),
            github_search=GitHubSearchConfig.model_validate(
                sources_data.get("github_search", {})
            ),
        )
    except ValidationError as exc:
        message = str(exc)
        if "category" in message:
            raise ValueError(f"频道分类无效，仅允许央视、地方频道、香港、台湾：{message}") from exc
        raise ValueError(f"配置校验失败：{message}") from exc
