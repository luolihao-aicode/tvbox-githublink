from pathlib import Path

from iptv_manager.cli import main


def test_validate_output_rejects_playlist_without_channels(tmp_path: Path, capsys) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "combined.m3u").write_text("#EXTM3U\n", encoding="utf-8")

    exit_code = main(["validate-output", "--output-dir", str(output)])

    assert exit_code == 2
    assert "没有频道" in capsys.readouterr().err
