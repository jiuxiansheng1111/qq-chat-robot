"""Register locally verified Markdown provenance, without fetching or uploading content."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.mahjong.knowledge import register_source
from app.services.mahjong.records import RecordError


def main() -> int:
    parser = argparse.ArgumentParser(description="雀魂知识Markdown可追溯导入")
    parser.add_argument("markdown", type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--kind", choices=["rules", "teaching", "video", "prompt"], required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--verified-by", default="")
    parser.add_argument("--verified", action="store_true")
    parser.add_argument("--timecode", action="append", default=[])
    args = parser.parse_args()
    try:
        value = register_source(
            Path(__file__).resolve().parents[1],
            args.markdown,
            title=args.title,
            kind=args.kind,
            source=args.source,
            verified_by=args.verified_by,
            timecodes=tuple(args.timecode),
            verified=args.verified,
        )
    except (RecordError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"登记{value.id}，状态{value.status}；内容SHA256={value.sha256}；没有训练或联网")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
