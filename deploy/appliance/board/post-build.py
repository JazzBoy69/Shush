#!/usr/bin/env python3
"""Finalize only the generated target filesystem, never the host OS."""

import os
from pathlib import Path
import shutil
import sys


def main():
    target = Path(sys.argv[1]).resolve()
    if target == Path("/") or not (target / "etc/os-release").is_file():
        raise SystemExit("Not a Buildroot target filesystem")
    project = Path(__file__).resolve().parents[3]
    unit = target / "etc/systemd/system/shush.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(project / "deploy/shush.service", unit)
    # Retire the prior manual-only launcher in reused development build trees.
    (target / "usr/libexec/shush-wait-devices").unlink(missing_ok=True)
    # Remove FTP units and enabled links from reused development trees.
    for name in (
        "shush-ftp.service",
        "var-lib-shushmedia.mount",
        "var-lib-shush-audio.mount",
    ):
        (unit.parent / name).unlink(missing_ok=True)
        for parent in ("local-fs.target.wants", "multi-user.target.wants"):
            (unit.parent / parent / name).unlink(missing_ok=True)
    for directory in ("boot", "var/lib/shush"):
        (target / directory).mkdir(parents=True, exist_ok=True)
    for path in (target / "opt/shush/reference").glob("audio_*json"):
        path.unlink()
    for path in (
        target / "var/lib/shushmedia",
        target / "usr/share/factory/var/lib/shushmedia",
    ):
        if path.exists():
            shutil.rmtree(path)
    for name in (
        "getty@.service",
        "serial-getty@.service",
        "console-getty.service",
        "debug-shell.service",
    ):
        path = unit.parent / name
        if path.is_symlink() or path.exists():
            path.unlink()
        path.symlink_to("/dev/null")
    # Read-only /etc: PID 1 supplies a transient machine ID in /run.
    (target / "etc/machine-id").write_text("")
    localtime = target / "etc/localtime"
    if localtime.exists() or localtime.is_symlink():
        localtime.unlink()
    localtime.symlink_to("../usr/share/zoneinfo/America/Chicago")
    if not (target / "usr/share/zoneinfo/America/Chicago").is_file():
        raise SystemExit("Missing Chicago timezone data")
    # Mutable DNS state stays in /run, even with a read-only /etc.
    resolv = target / "etc/resolv.conf"
    if resolv.exists() or resolv.is_symlink():
        resolv.unlink()
    resolv.symlink_to("../run/systemd/resolve/stub-resolv.conf")


if __name__ == "__main__":
    main()
