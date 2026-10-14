from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Sequence

from .pipeline import PipelineOptions, run_pipeline
from .models import ChannelCategory
from .publisher import OutputValidationError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="IPTV 播放源自动检测与发布工具")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("update", "抓取、检测并更新订阅"),
        ("discover", "搜索 GitHub 新来源并更新候选池"),
    ):
        sub = subparsers.add_parser(command, help=help_text)
        sub.add_argument("--config-dir", type=Path, default=Path("config"))
        sub.add_argument("--data-dir", type=Path, default=Path("data"))
        sub.add_argument("--output-dir", type=Path, default=Path("output"))
        sub.add_argument("--dry-run", action="store_true", help="只运行检测，不写入文件")
        sub.add_argument('--category', choices=[item.value for item in ChannelCategory],
                         help='只复检指定分类，其他分类保留原检测记录')
    validate = subparsers.add_parser("validate-output", help="校验已生成订阅")
    validate.add_argument("--output-dir", type=Path, default=Path("output"))
    trial = subparsers.add_parser('trial-taiwan', help='收集台湾 1080p 候选线路并生成独立试播订阅')
    trial.add_argument('--config-dir', type=Path, default=Path('config'))
    trial.add_argument('--sources-file', type=Path, default=Path('config/taiwan_trial_sources.yml'))
    trial.add_argument('--output-dir', type=Path, default=Path('output'))
    return parser


def _validate_output(output_dir: Path) -> int:
    combined = output_dir / "combined.m3u"
    if not combined.is_file():
        print("错误：缺少 combined.m3u", file=sys.stderr)
        return 2
    text = combined.read_text(encoding="utf-8")
    if not text.startswith("#EXTM3U") or "#EXTINF:" not in text:
        print("错误：综合订阅没有频道或格式无效", file=sys.stderr)
        return 2
    print("订阅文件校验通过")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "validate-output":
        return _validate_output(args.output_dir)
    if args.command == 'trial-taiwan':
        from .taiwan_trial import run_taiwan_trial
        try:
            asyncio.run(run_taiwan_trial(args.config_dir, args.sources_file, args.output_dir))
        except (ValueError, OSError) as exc:
            print(f'错误：{exc}', file=sys.stderr)
            return 2
        return 0
    options = PipelineOptions(
        config_dir=args.config_dir,
        data_dir=args.data_dir,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
        discover_github=args.command == "discover",
        github_token=os.environ.get("GITHUB_TOKEN", ""),
        categories=(ChannelCategory(args.category),) if args.category else (),
    )
    try:
        result = asyncio.run(run_pipeline(options))
    except (ValueError, OutputValidationError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 2
    print(f"检测完成：{len(result.candidates)} 个候选源，发布 {len(result.selections)} 个频道")
    for issue in result.issues:
        print(f'来源发现问题：{issue.code}', file=sys.stderr)
    for code, count in result.report.get('filter_reasons', {}).items():
        print(f'筛选结果：{code}，{count} 个源')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
