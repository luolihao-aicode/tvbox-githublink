from datetime import UTC, datetime

import pytest

from iptv_manager.models import SourceType, StreamCandidate
from iptv_manager.normalize import make_stream_id


@pytest.fixture
def candidate_factory():
    def make(url: str, raw_name: str) -> StreamCandidate:
        return StreamCandidate(
            stream_id=make_stream_id(url),
            raw_name=raw_name,
            url=url,
            source_url="https://list.example/channels.m3u",
            source_type=SourceType.COMMUNITY,
            discovered_at=datetime(2026, 8, 25, tzinfo=UTC),
        )

    return make
