"""Pure session state machine for the Raspberry Pi bedtime audio app."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence
from zoneinfo import ZoneInfo


SCHEDULE_ANCHOR = datetime(2020, 4, 5, 3, 0)
SCHEDULE_CYCLE_DAYS = 366
UTC = timezone.utc


class Phase(str, Enum):
    IDLE = "idle"
    BEDTIME_SETUP = "bedtime_setup"
    PLAYLIST = "playlist"
    PINK_NOISE = "pink_noise"
    STOPPED = "stopped"


class StartChoice(str, Enum):
    READINGS = "readings"
    PINK_NOISE = "pink_noise"


@dataclass(frozen=True)
class Session:
    phase: Phase = Phase.IDLE
    alarm_at: datetime | None = None
    start_choice: StartChoice | None = None
    schedule_day: int | None = None
    track_index: int = 0
    track_count: int = 0


Schedule = Mapping[str, Sequence[object]]


def system_timezone() -> ZoneInfo:
    """Load the timezone configured by Raspberry Pi OS from /etc/localtime."""
    localtime = Path("/etc/localtime")
    try:
        resolved = localtime.resolve(strict=True)
        parts = resolved.parts
        zoneinfo_index = parts.index("zoneinfo")
        key = "/".join(parts[zoneinfo_index + 1 :])
        if key:
            return ZoneInfo(key)
    except (OSError, ValueError):
        pass

    # Some OS images copy the zone file instead of symlinking it. ZoneInfo.from_file
    # still reads the OS-provided timezone rules without an app config file.
    with localtime.open("rb") as zone_file:
        return ZoneInfo.from_file(zone_file, key="system-localtime")


def schedule_day(now: datetime) -> int:
    """Return the legacy anchored day index, including all 366 schedule entries.

    ``now`` must already be expressed in the timezone supplied by the OS. Day
    boundaries intentionally follow the legacy implementation's elapsed 24-hour
    calculation, including its behavior across daylight-saving transitions.
    """
    _require_aware(now)
    anchor = SCHEDULE_ANCHOR.replace(tzinfo=now.tzinfo)
    elapsed = now.astimezone(UTC) - anchor.astimezone(UTC)
    return (elapsed // timedelta(days=1)) % SCHEDULE_CYCLE_DAYS


def begin_setup(session: Session) -> Session:
    """Enter bedtime setup from idle or after a stopped session."""
    if session.phase not in (Phase.IDLE, Phase.STOPPED):
        raise ValueError(f"cannot begin setup while session is {session.phase.value}")
    return Session(phase=Phase.BEDTIME_SETUP)


def set_alarm(session: Session, now: datetime, alarm_text: str) -> Session:
    """Set this session's alarm from an HH:MM local-time input."""
    if session.phase is not Phase.BEDTIME_SETUP:
        raise ValueError("alarm time can only be set during bedtime setup")
    _require_aware(now)
    try:
        alarm_time = datetime.strptime(alarm_text, "%H:%M").time()
    except ValueError as error:
        raise ValueError("alarm time must use 24-hour HH:MM format") from error

    candidate = datetime.combine(now.date(), alarm_time, tzinfo=now.tzinfo)
    if _is_nonexistent_local_time(candidate):
        raise ValueError("alarm time falls in a daylight-saving clock-change gap")
    if candidate.astimezone(UTC) <= now.astimezone(UTC):
        candidate_date = now.date() + timedelta(days=1)
        candidate = datetime.combine(candidate_date, alarm_time, tzinfo=now.tzinfo)
        if _is_nonexistent_local_time(candidate):
            raise ValueError("alarm time falls in a daylight-saving clock-change gap")
    return replace(session, alarm_at=candidate)


def start_session(
    session: Session,
    now: datetime,
    choice: StartChoice,
    schedule: Schedule,
) -> Session:
    """Start readings or pink noise using the session alarm already entered."""
    if session.phase is not Phase.BEDTIME_SETUP or session.alarm_at is None:
        raise ValueError("set an alarm during bedtime setup before starting audio")
    _require_aware(now)
    if now.astimezone(UTC) >= session.alarm_at.astimezone(UTC):
        return replace(session, phase=Phase.STOPPED, start_choice=choice)

    if choice is StartChoice.PINK_NOISE:
        return replace(session, phase=Phase.PINK_NOISE, start_choice=choice)

    day = schedule_day(now)
    entries = schedule.get(str(day))
    if not entries:
        raise ValueError(f"schedule day {day} is missing or empty")
    return replace(
        session,
        phase=Phase.PLAYLIST,
        start_choice=choice,
        schedule_day=day,
        track_index=0,
        track_count=len(entries),
    )


def advance_time(session: Session, now: datetime) -> Session:
    """Apply the hard alarm cutoff while either playback phase is active."""
    _require_aware(now)
    if session.phase in (Phase.PLAYLIST, Phase.PINK_NOISE):
        if session.alarm_at is None:
            raise ValueError("active session has no alarm deadline")
        if now.astimezone(UTC) >= session.alarm_at.astimezone(UTC):
            return replace(session, phase=Phase.STOPPED)
    return session


def track_finished(session: Session, now: datetime) -> Session:
    """Advance after a full track or a scheduled partial-track segment ends."""
    session = advance_time(session, now)
    if session.phase is not Phase.PLAYLIST:
        return session
    if session.track_index + 1 < session.track_count:
        return replace(session, track_index=session.track_index + 1)
    return replace(session, phase=Phase.PINK_NOISE, start_choice=StartChoice.PINK_NOISE)


def stop_session(session: Session) -> Session:
    """Stop audio immediately, regardless of the current session phase."""
    return replace(session, phase=Phase.STOPPED)


def _is_nonexistent_local_time(value: datetime) -> bool:
    """Detect a local clock time skipped by a daylight-saving transition."""
    round_trip = value.astimezone(UTC).astimezone(value.tzinfo)
    return round_trip.replace(tzinfo=None) != value.replace(tzinfo=None)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("current time must be timezone-aware")
