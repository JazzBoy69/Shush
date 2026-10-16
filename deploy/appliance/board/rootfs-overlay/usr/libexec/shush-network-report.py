#!/usr/bin/python3
"""Retain headless network failure evidence on the readable boot partition."""
from pathlib import Path
import os
import subprocess
import time


UNITS = (
    "shush-wifi.service", "systemd-networkd.service", "systemd-resolved.service",
    "systemd-timesyncd.service", "systemd-time-wait-sync.service", "shush.service",
)


def command(arguments):
    try:
        result = subprocess.run(arguments, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, timeout=5)
        return result.stdout.decode(errors="replace")[-131072:]
    except (OSError, subprocess.TimeoutExpired) as error:
        return f"{error}\n"


def snapshot():
    commands = [
        ["date", "-u"],
        ["systemctl", "show", "--no-pager",
         "--property=Id,ActiveState,SubState,Result,ExecMainStatus,StatusText", *UNITS],
        ["networkctl", "status", "--all", "--no-pager"],
        ["ip", "-4", "route"],
        ["timedatectl", "show"],
        ["resolvectl", "status"],
        ["journalctl", "-b", "--no-pager", "-n", "400",
         *[part for unit in UNITS for part in ("-u", unit)]],
        ["dmesg"],
    ]
    # Never read the private Wi-Fi or MQTT configuration files.
    return "\n".join("$ " + " ".join(args) + "\n" + command(args)
                     for args in commands)


def save(data):
    mounts = [line.split() for line in Path("/proc/mounts").read_text().splitlines()]
    boot = next(entry for entry in mounts if entry[1] == "/boot")
    was_read_only = "ro" in boot[3].split(",")
    if was_read_only:
        subprocess.run(["mount", "-o", "remount,rw", "/boot"], check=True, timeout=5)
    try:
        temporary = Path("/boot/.shush-network-report.txt.tmp")
        with temporary.open("w") as report:
            report.write(data)
            report.flush()
            os.fsync(report.fileno())
        temporary.replace(Path("/boot/shush-network-report.txt"))
    finally:
        if was_read_only:
            subprocess.run(["mount", "-o", "remount,ro", "/boot"], check=True, timeout=5)


def main():
    data = "Shush clock/network failure report\n"
    for delay in (0, 45):
        time.sleep(delay)
        data += "\n=== Snapshot ===\n" + snapshot()
        try:
            save(data)
        except (OSError, StopIteration, subprocess.SubprocessError) as error:
            print(f"Could not retain network report: {error}", flush=True)
            print(data, flush=True)


if __name__ == "__main__":
    main()
