from datetime import UTC, datetime, timedelta

from iptv_manager.models import CheckResult, PolicyConfig, StreamStatus
from iptv_manager.scoring import append_history, calculate_score, select_lines


def _success(checked_at: datetime, *, first_byte: float = 1.0, height: int = 1080) -> CheckResult:
    return CheckResult(
        checked_at=checked_at,
        success=True,
        first_byte_seconds=first_byte,
        transfer_bytes_per_second=2_000_000,
        width=1920,
        height=height,
        bitrate=4_500_000,
        continuous_read=True,
    )


def test_history_keeps_latest_thirty() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    results = [_success(start + timedelta(days=index)) for index in range(31)]
    history: tuple[CheckResult, ...] = ()

    for result in results:
        history = append_history(history, result)

    assert len(history) == 30
    assert history[0] == results[1]
    assert history[-1] == results[30]


def test_stable_fast_1080p_stream_scores_above_unstable_stream() -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    stable = [_success(now - timedelta(days=index)) for index in range(10)]
    unstable = [
        result if index % 2 == 0 else result.model_copy(update={"success": False, "continuous_read": False})
        for index, result in enumerate(stable)
    ]

    assert calculate_score(stable, PolicyConfig()) > calculate_score(unstable, PolicyConfig())


def test_select_lines_keeps_one_primary_and_two_backups(candidate_factory) -> None:
    now = datetime(2026, 8, 25, tzinfo=UTC)
    streams = []
    for index, delay in enumerate((0.5, 1.0, 1.5, 2.0)):
        stream = candidate_factory(f"https://stream.example/{index}.m3u8", "CCTV-1")
        stream.channel_id = "cctv-1"
        stream.channel_name = "CCTV-1 综合"
        stream.category = "央视"
        stream.history = [_success(now - timedelta(days=1), first_byte=delay), _success(now, first_byte=delay)]
        stream.score = calculate_score(stream.history, PolicyConfig())
        streams.append(stream)

    selection = select_lines(streams, PolicyConfig())[0]

    assert selection.primary.status is StreamStatus.ACTIVE
    assert len(selection.backups) == 2
    assert all(item.status is StreamStatus.BACKUP for item in selection.backups)

