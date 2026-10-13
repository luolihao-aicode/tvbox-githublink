from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ChannelCategory(StrEnum):
    CCTV = "央视"
    SATELLITE = "地方频道"
    HONG_KONG = "香港"
    TAIWAN = "台湾"


class SourceType(StrEnum):
    OFFICIAL = "official"
    COMMUNITY = "community"


class StreamStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    BACKUP = "backup"
    COOLING = "cooling"
    RETIRED = "retired"


class SourceConfig(StrictModel):
    url: HttpUrl
    type: SourceType = SourceType.COMMUNITY
    enabled: bool = True
    max_bytes: int = Field(default=2_000_000, ge=1, le=10_000_000)
    categories: list[ChannelCategory] = Field(default_factory=list)


class GitHubSearchConfig(StrictModel):
    enabled: bool = True
    queries: list[str] = Field(default_factory=lambda: ["extension:m3u IPTV"])
    max_files: int = Field(default=50, ge=1, le=500)
    max_pages: int = Field(default=3, ge=1, le=10)
    max_bytes: int = Field(default=2_000_000, ge=1, le=10_000_000)


class ChannelRule(StrictModel):
    id: Annotated[str, Field(min_length=1, pattern=r"^[a-z0-9-]+$")]
    name: Annotated[str, Field(min_length=1)]
    category: ChannelCategory
    aliases: list[str] = Field(default_factory=list)
    minimum_height: int | None = Field(default=None, ge=540)


class PolicyConfig(StrictModel):
    http_timeout_seconds: float = Field(default=5.0, gt=0)
    media_probe_seconds: float = Field(default=8.0, gt=0)
    media_concurrency: int = Field(default=4, ge=1, le=16)
    history_limit: int = Field(default=30, ge=1)
    minimum_height: int = Field(default=720, ge=540)
    switch_margin: float = Field(default=8.0, ge=0, le=100)
    global_concurrency: int = Field(default=30, ge=1)
    per_host_concurrency: int = Field(default=2, ge=1)
    rejected_streams: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_concurrency(self) -> "PolicyConfig":
        if self.per_host_concurrency > self.global_concurrency:
            raise ValueError("单域名并发数不能超过全局并发数")
        return self


class CheckResult(StrictModel):
    checked_at: datetime
    success: bool
    failure_code: str | None = None
    first_byte_seconds: float | None = Field(default=None, ge=0)
    startup_seconds: float | None = Field(default=None, ge=0)
    transfer_bytes_per_second: float | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)
    video_codec: str | None = None
    audio_codec: str | None = None
    bitrate: int | None = Field(default=None, ge=0)
    continuous_read: bool = False


class StreamCandidate(StrictModel):
    stream_id: str
    channel_id: str | None = None
    channel_name: str | None = None
    raw_name: str
    category: ChannelCategory | None = None
    url: HttpUrl
    source_url: HttpUrl
    source_type: SourceType
    protocol: str | None = None
    discovered_at: datetime
    last_checked_at: datetime | None = None
    last_success_at: datetime | None = None
    status: StreamStatus = StreamStatus.CANDIDATE
    history: list[CheckResult] = Field(default_factory=list)
    score: float = Field(default=0, ge=0, le=100)


class AppConfig(StrictModel):
    sources: list[SourceConfig]
    channels: list[ChannelRule]
    policy: PolicyConfig
    github_search: GitHubSearchConfig = Field(default_factory=GitHubSearchConfig)
