"""Audit the static anime/Galgame catalog before it is published."""

import argparse
import json
import unicodedata
from pathlib import Path

from app.services.anime_character import ANIME_CHARACTER_ROSTER


def key(value: str) -> str:
    return "".join(unicodedata.normalize("NFKC", value or "").casefold().split())


def run(output: Path, minimum: int) -> int:
    names: dict[str, list[str]] = {}
    aliases: dict[str, list[str]] = {}
    short: list[str] = []
    for character in ANIME_CHARACTER_ROSTER:
        names.setdefault(key(character.name), []).append(character.name)
        for alias in character.aliases:
            aliases.setdefault(key(alias), []).append(character.name)
        if len(character.description.strip()) < 20:
            short.append(character.name)
    duplicate_names = [values for values in names.values() if len(values) > 1]
    alias_conflicts = [values for values in aliases.values() if len(set(values)) > 1]
    payload = {
        "total": len(ANIME_CHARACTER_ROSTER),
        "minimum": minimum,
        "minimum_ok": len(ANIME_CHARACTER_ROSTER) >= minimum,
        "duplicate_names": duplicate_names,
        "alias_conflicts": alias_conflicts,
        "short_descriptions": short,
        "entries_without_aliases": [item.name for item in ANIME_CHARACTER_ROSTER if not item.aliases],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        f"Anime catalog audit: total={payload['total']} minimum={minimum} "
        f"duplicate_names={len(duplicate_names)} alias_conflicts={len(alias_conflicts)} "
        f"short_descriptions={len(short)}"
    )
    return 0 if payload["minimum_ok"] and not duplicate_names and not alias_conflicts else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit anime/Galgame catalog quality")
    parser.add_argument("--output", default="data/anime-catalog-audit.json")
    parser.add_argument("--minimum", type=int, default=1000)
    args = parser.parse_args()
    return run(Path(args.output), max(0, args.minimum))


if __name__ == "__main__":
    raise SystemExit(main())
