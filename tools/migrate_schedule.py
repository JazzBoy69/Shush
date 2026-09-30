#!/usr/bin/env python3
"""Convert the legacy readingSchedule JavaScript object to portable JSON."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ANCHOR_LOCAL = "2020-04-05T03:00:00"
CYCLE_DAYS = 366


def load_legacy_schedule(path: Path) -> dict[str, list[str]]:
    source = path.read_text(encoding="utf-8")
    match = re.fullmatch(
        r"\s*var\s+readingSchedule\s*=\s*(\{.*\})\s*;?\s*",
        source,
        flags=re.DOTALL,
    )
    if not match:
        raise ValueError(f"{path} does not contain a readingSchedule object")

    # The legacy object is JSON-compatible apart from JavaScript trailing commas.
    object_text = re.sub(r",(\s*[}\]])", r"\1", match.group(1))
    schedule = json.loads(object_text)
    if not isinstance(schedule, dict):
        raise ValueError("readingSchedule must be an object keyed by day index")
    return schedule


def convert_entry(value: object, day: str, index: int) -> dict[str, object]:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"day {day}, entry {index}: expected a nonempty string")

    parts = [part.strip() for part in value.split(",")]
    filename = parts[0]
    if not filename or Path(filename).name != filename:
        raise ValueError(f"day {day}, entry {index}: expected a filename, got {filename!r}")

    if len(parts) == 1:
        return {"file": filename}
    if len(parts) != 3:
        raise ValueError(f"day {day}, entry {index}: malformed clip specification {value!r}")

    try:
        start_seconds, duration_seconds = int(parts[1]), int(parts[2])
    except ValueError as error:
        raise ValueError(f"day {day}, entry {index}: clip offsets must be integers") from error
    if start_seconds < 0 or duration_seconds <= 0:
        raise ValueError(f"day {day}, entry {index}: invalid clip range {value!r}")
    return {
        "file": filename,
        "start_seconds": start_seconds,
        "duration_seconds": duration_seconds,
    }


def migrate(source: Path, output: Path, media_dir: Path | None) -> tuple[int, int, int, list[str]]:
    legacy = load_legacy_schedule(source)
    expected_keys = {str(day) for day in range(CYCLE_DAYS)}
    actual_keys = set(legacy)
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys, key=int)
        extra = sorted(actual_keys - expected_keys, key=int)
        raise ValueError(f"schedule keys must be 0..{CYCLE_DAYS - 1}; missing={missing}, extra={extra}")

    days: dict[str, list[dict[str, object]]] = {}
    clip_count = 0
    filenames: set[str] = set()
    for day in range(CYCLE_DAYS):
        key = str(day)
        entries = legacy[key]
        if not isinstance(entries, list) or not entries:
            raise ValueError(f"day {key}: expected a nonempty list")
        converted = [convert_entry(entry, key, index) for index, entry in enumerate(entries)]
        days[key] = converted
        for entry in converted:
            filenames.add(str(entry["file"]))
            if "start_seconds" in entry:
                clip_count += 1

    missing_files = sorted(
        filename for filename in filenames if media_dir is not None and not (media_dir / filename).is_file()
    )
    document = {
        "version": 1,
        "anchor_local": ANCHOR_LOCAL,
        "cycle_days": CYCLE_DAYS,
        "days": days,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return len(days), sum(map(len, days.values())), clip_count, missing_files


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "schedule.js")
    parser.add_argument("--output", type=Path, default=ROOT / "reading_schedule.json")
    parser.add_argument("--media-dir", type=Path, default=ROOT / "Audio")
    parser.add_argument(
        "--skip-media-check",
        action="store_true",
        help="validate and convert schedule entries without checking the Audio directory",
    )
    args = parser.parse_args()

    days, entries, clips, missing_files = migrate(
        args.source,
        args.output,
        None if args.skip_media_check else args.media_dir,
    )
    print(f"Wrote {args.output}: {days} days, {entries} entries, {clips} partial-track clips")
    if missing_files:
        sample = ", ".join(missing_files[:10])
        suffix = " ..." if len(missing_files) > 10 else ""
        print(f"Warning: {len(missing_files)} referenced media files are missing from {args.media_dir}: {sample}{suffix}")


if __name__ == "__main__":
    main()
