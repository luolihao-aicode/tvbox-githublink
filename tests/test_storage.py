from iptv_manager.storage import atomic_write_text


def test_atomic_write_replaces_file_with_utf8_text(tmp_path) -> None:
    target = tmp_path / "data" / "status.json"

    atomic_write_text(target, '{"状态":"正常"}\n')

    assert target.read_text(encoding="utf-8") == '{"状态":"正常"}\n'
    assert list(target.parent.glob("*.tmp")) == []
