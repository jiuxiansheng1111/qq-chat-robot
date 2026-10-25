"""Offline CLI. Does not initialize the robot, read .env, or perform networking."""

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.mahjong.records import RecordError, parse_record, read_local
from app.services.mahjong.review import markdown, review


def main() -> int:
    parser = argparse.ArgumentParser(description="本地雀魂四麻赛后复盘（无外部请求）")
    parser.add_argument("record", type=Path)
    parser.add_argument("--seat", required=True, help="开局固定座位，0东1南2西3北")
    parser.add_argument("--match", choices=["东风", "半庄", "tonpuu", "hanchan"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--visible-json", type=Path, help="可选导出已冻结的当时视角计算证据，不含事后暗手"
    )
    args = parser.parse_args()
    try:
        result = review(parse_record(read_local(args.record), match=args.match), args.seat)
        args.output.write_text(markdown(result), encoding="utf-8")
        if args.visible_json:
            args.visible_json.write_text(
                json.dumps(asdict(result.analysis), ensure_ascii=False, indent=2), encoding="utf-8"
            )
    except (RecordError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"已分析{len(result.analysis.decisions)}个决策；输出 {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
