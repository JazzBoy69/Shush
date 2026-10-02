"""Command-line entry point for service and local session controls."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Sequence

from .service import ServiceError, run_service, send_control_request


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bedtime-audio")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("service", help="run the long-lived system service")

    start = commands.add_parser("start", help="start this session's readings or pink noise")
    start.add_argument("--alarm", required=True, metavar="HH:MM", help="local alarm time, 24-hour format")
    start.add_argument(
        "--mode",
        choices=("readings", "pink-noise"),
        default="readings",
        help="start the daily playlist or go directly to pink noise",
    )

    commands.add_parser("stop", help="stop the current session")
    commands.add_parser("status", help="show current session state")
    commands.add_parser("pause", help="pause current audio")
    commands.add_parser("resume", help="resume current audio")

    volume = commands.add_parser("volume", help="set playback volume")
    volume.add_argument("percent", type=float, help="volume from 0 through 100")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "service":
        run_service()
        return 0
    if args.command == "start":
        request = {
            "action": "start",
            "alarm": args.alarm,
            "mode": "pink_noise" if args.mode == "pink-noise" else "readings",
        }
    elif args.command == "volume":
        request = {"action": "volume", "percent": args.percent}
    else:
        request = {"action": args.command}

    try:
        response = send_control_request(request)
    except ServiceError as error:
        print(f"bedtime-audio: {error}", file=sys.stderr)
        return 1
    print(json.dumps(response, indent=2, ensure_ascii=False))
    return 0 if response.get("ok") is True else 1


if __name__ == "__main__":
    raise SystemExit(main())
