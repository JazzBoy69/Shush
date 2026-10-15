#!/usr/bin/env python3
"""Finalize only the generated target filesystem, never the host OS."""

import os
from pathlib import Path
import shutil
import sys


def install_private_network(target, source):
    """Copy secrets separately from the tracked overlay and boot partition."""
    private = target / "etc/shush-private"
    # Remove stale configuration if the builder switches private directories.
    if private.exists():
        shutil.rmtree(private)
    mqtt_unit = target / "etc/systemd/system/shush.service.d/mqtt.conf"
    mqtt_unit.unlink(missing_ok=True)
    if source is None:
        return
    source = Path(source).resolve()
    names = ("wpa_supplicant.conf", "mqtt/mosquitto_sub", "mqtt/mosquitto_pub", "mqtt/topic")
    if not all((source / name).is_file() for name in names):
        raise SystemExit("Incomplete private network configuration")
    private.mkdir(parents=True, mode=0o700)
    (private / "mqtt").mkdir(mode=0o700)
    for name in names:
        shutil.copyfile(source / name, private / name)
        (private / name).chmod(0o600)
    mqtt_unit.parent.mkdir(parents=True, exist_ok=True)
    mqtt_unit.write_text(
        "[Service]\nLoadCredential=mqtt:/etc/shush-private/mqtt\n"
        "RestrictAddressFamilies=\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n"
        "TasksMax=32\n"
    )


def main():
    target = Path(sys.argv[1]).resolve()
    if target == Path("/") or not (target / "etc/os-release").is_file():
        raise SystemExit("Not a Buildroot target filesystem")
    project = Path(__file__).resolve().parents[3]
    unit = target / "etc/systemd/system/shush.service"
    unit.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(project / "deploy/shush.service", unit)
    install_private_network(target, os.environ.get("SHUSH_PRIVATE_CONFIG"))
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
