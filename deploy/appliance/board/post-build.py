#!/usr/bin/env python3
"""Finalize only the generated target filesystem, never the host OS."""

import os
from pathlib import Path
import shutil
import sys
import tempfile


def install_private_network(target, source):
    """Copy secrets separately from the tracked overlay and boot partition."""
    private = target / "etc/shush-private"
    mqtt_unit = target / "etc/systemd/system/shush.service.d/mqtt.conf"
    if source is None:
        # A direct Buildroot invocation may omit the builder's environment.
        # Never interpret that as a request to delete installed credentials.
        return
    source = Path(source).resolve()
    names = ("wpa_supplicant.conf", "mqtt/mosquitto_sub", "mqtt/mosquitto_pub", "mqtt/topic")
    if not all((source / name).is_file() for name in names):
        raise SystemExit("Incomplete private network configuration")
    private.parent.mkdir(parents=True, exist_ok=True)
    # Finish all copies before moving the existing directory. Retain the old
    # directory as a recovery copy, including if the process is interrupted.
    with tempfile.TemporaryDirectory(prefix=".shush-private-", dir=private.parent) as folder:
        staged = Path(folder) / "new"
        staged.mkdir(mode=0o700)
        (staged / "mqtt").mkdir(mode=0o700)
        for name in names:
            shutil.copyfile(source / name, staged / name)
            (staged / name).chmod(0o600)
        mqtt_unit.parent.mkdir(parents=True, exist_ok=True)
        staged_unit = Path(folder) / "mqtt.conf"
        staged_unit.write_text(
            "[Service]\n"
            "LoadCredential=mosquitto_sub:/etc/shush-private/mqtt/mosquitto_sub\n"
            "LoadCredential=mosquitto_pub:/etc/shush-private/mqtt/mosquitto_pub\n"
            "LoadCredential=topic:/etc/shush-private/mqtt/topic\n"
            "RestrictAddressFamilies=\nRestrictAddressFamilies=AF_UNIX AF_INET AF_INET6\n"
            "TasksMax=32\n"
        )
        backup = None
        if private.exists():
            backup = Path(tempfile.mkdtemp(prefix=".shush-private-backup-", dir=target.parent))
            if mqtt_unit.exists():
                shutil.copyfile(mqtt_unit, backup / "mqtt.conf")
            private.rename(backup / "private")
        try:
            staged.rename(private)
            os.replace(staged_unit, mqtt_unit)
        except BaseException:
            if private.exists():
                private.rename(staged)
            if backup is not None:
                (backup / "private").rename(private)
            raise



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
    (target / "var/lib/bluetooth").mkdir(parents=True, exist_ok=True)
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
