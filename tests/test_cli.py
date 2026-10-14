from pathlib import Path

from iptv_manager.cli import main


def test_validate_output_rejects_playlist_without_channels(tmp_path: Path, capsys) -> None:
    output = tmp_path / "output"
    output.mkdir()
    (output / "combined.m3u").write_text("#EXTM3U\n", encoding="utf-8")

    exit_code = main(["validate-output", "--output-dir", str(output)])

    assert exit_code == 2
    assert "没有频道" in capsys.readouterr().err


def test_trial_reports_missing_config_without_uncaught_exception(tmp_path, capsys):
    code = main(['trial-taiwan', '--config-dir', str(tmp_path / 'missing')])
    assert code == 2
    assert '缺少配置文件' in capsys.readouterr().err
