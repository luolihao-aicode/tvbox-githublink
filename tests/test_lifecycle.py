from datetime import UTC, datetime, timedelta

from iptv_manager.models import CheckResult, PolicyConfig, StreamStatus
from iptv_manager.scoring import update_lifecycle


def _failure(checked_at: datetime) -> CheckResult:
    return CheckResult(checked_at=checked_at, success=False, failure_code="HTTP_TIMEOUT")


def test_two_consecutive_failures_move_stream_to_cooling(candidate_factory) -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    stream = candidate_factory("https://stream.example/live.m3u8", "CCTV-1")
    stream.status = StreamStatus.ACTIVE
    stream.history = [_failure(now - timedelta(days=1)), _failure(now)]

    updated = update_lifecycle(stream, now, PolicyConfig())

    assert updated.status is StreamStatus.COOLING


def test_thirty_days_without_success_retires_stream(candidate_factory) -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    stream = candidate_factory("https://stream.example/live.m3u8", "CCTV-1")
    stream.status = StreamStatus.COOLING
    stream.discovered_at = now - timedelta(days=31)
    stream.history = [_failure(now - timedelta(days=30)), _failure(now)]

    updated = update_lifecycle(stream, now, PolicyConfig())

    assert updated.status is StreamStatus.RETIRED
