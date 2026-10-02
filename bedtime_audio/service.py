"""Long-running bedtime audio service and private local control socket."""

from __future__ import annotations

import json
import logging
import os
import queue
import signal
import socket
import socketserver
import stat
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .planner import (
    Phase,
    Session,
    StartChoice,
    advance_time,
    begin_setup,
    set_alarm,
    start_session,
    stop_session,
    system_timezone,
    track_finished,
)
from .key_listener import MediaKeyListener
from .audio_control import AudioClient, PlayerError, PlayerEvent


LOG = logging.getLogger(__name__)
CONTROL_SOCKET = Path("/run/bedtime-audio/control.sock")
SCHEDULE_FILE = Path("/opt/bedtime-audio/data/reading_schedule.json")
MEDIA_ROOT = Path("/var/lib/bedtime-audio/Audio")
PINK_NOISE_FILE = Path("/var/lib/bedtime-audio/Pink_Noise.wav")
MAX_CONTROL_MESSAGE_BYTES = 8192
SESSION_STATE_FILE = Path("/var/lib/bedtime-audio/session-state.json")


class ServiceError(RuntimeError):
    """Raised when the service cannot initialize or apply a control request."""


def load_schedule(path: Path = SCHEDULE_FILE) -> Mapping[str, list[dict[str, Any]]]:
    """Load and validate the installed 366-day JSON schedule and media files."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ServiceError(f"cannot read schedule {path}: {error}") from error
    except json.JSONDecodeError as error:
        raise ServiceError(f"schedule is not valid JSON: {path}:{error.lineno}") from error

    if not isinstance(document, dict):
        raise ServiceError("schedule document must be a JSON object")
    if document.get("version") != 1 or document.get("cycle_days") != 366:
        raise ServiceError("schedule must use version 1 and a 366-day cycle")
    days = document.get("days")
    if not isinstance(days, dict) or set(days) != {str(day) for day in range(366)}:
        raise ServiceError("schedule must contain every day index from 0 through 365")

    missing: set[str] = set()
    for day, entries in days.items():
        if not isinstance(entries, list) or not entries:
            raise ServiceError(f"schedule day {day} must contain at least one entry")
        for index, entry in enumerate(entries):
            if not isinstance(entry, dict):
                raise ServiceError(f"schedule day {day}, entry {index} must be an object")
            filename = entry.get("file")
            if (
                not isinstance(filename, str)
                or not filename
                or filename in {".", ".."}
                or "/" in filename
                or "\\" in filename
                or Path(filename).name != filename
            ):
                raise ServiceError(f"schedule day {day}, entry {index} has an invalid file name")
            has_start = "start_seconds" in entry
            has_duration = "duration_seconds" in entry
            if has_start != has_duration:
                raise ServiceError(f"schedule day {day}, entry {index} has an incomplete clip range")
            if has_start:
                start = entry["start_seconds"]
                duration = entry["duration_seconds"]
                if not isinstance(start, (int, float)) or start < 0:
                    raise ServiceError(f"schedule day {day}, entry {index} has an invalid clip start")
                if not isinstance(duration, (int, float)) or duration <= 0:
                    raise ServiceError(f"schedule day {day}, entry {index} has an invalid clip duration")
            if not (MEDIA_ROOT / filename).is_file():
                missing.add(filename)
    if not PINK_NOISE_FILE.is_file():
        missing.add(str(PINK_NOISE_FILE))
    if missing:
        sample = ", ".join(sorted(missing)[:10])
        suffix = " ..." if len(missing) > 10 else ""
        raise ServiceError(f"{len(missing)} required media files are missing: {sample}{suffix}")
    return days


class _ControlHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        raw = self.rfile.readline(MAX_CONTROL_MESSAGE_BYTES + 1)
        if len(raw) > MAX_CONTROL_MESSAGE_BYTES or not raw.endswith(b"\n"):
            self._respond({"ok": False, "error": "control request is too large or missing its newline"})
            return
        try:
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("request must be a JSON object")
            response = self.server.owner.handle_request(request)  # type: ignore[attr-defined]
        except (json.JSONDecodeError, ValueError, ServiceError, PlayerError) as error:
            response = {"ok": False, "error": str(error)}
        except Exception:
            LOG.exception("Unexpected control request failure")
            response = {"ok": False, "error": "internal service error"}
        self._respond(response)

    def _respond(self, response: Mapping[str, object]) -> None:
        self.wfile.write(json.dumps(response, ensure_ascii=False).encode("utf-8") + b"\n")


class _ControlServer(socketserver.ThreadingUnixStreamServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, path: Path, owner: BedtimeService) -> None:
        self.owner = owner
        super().__init__(str(path), _ControlHandler, bind_and_activate=False)
        self.server_bind()
        os.chmod(path, 0o600)
        self.server_activate()


class BedtimeService:
    def __init__(self) -> None:
        self.timezone = system_timezone()
        self.schedule = load_schedule()
        self.player_events: queue.Queue[PlayerEvent] = queue.Queue()
        self.alarm_minutes = 6 * 60 + 30
        self.selected_choice = StartChoice.READINGS
        self.last_ui_error: str | None = None
        self.paused = False
        self.session, self.restore_audio_stop = self._load_persisted_session()
        self.state_lock = threading.RLock()
        self.stop_requested = threading.Event()
        self.audio_stop_pending = False
        self.audio_stop_retry_after = 0.0
        self.audio_event_poll_after = 0.0
        self.audio_poll_error_logged_at = 0.0
        self.player = AudioClient(self.player_events.put)
        self.control_server: _ControlServer | None = None
        self.control_thread: threading.Thread | None = None
        self.media_keys = MediaKeyListener(self.handle_media_key)

    def run(self) -> None:
        try:
            self._clear_stale_control_socket()
            self.player.start()
            if self.restore_audio_stop:
                with self.state_lock:
                    self._request_audio_stop()
                    self.restore_audio_stop = False
            self.control_server = _ControlServer(CONTROL_SOCKET, self)
            self.control_thread = threading.Thread(
                target=self.control_server.serve_forever,
                name="bedtime-control-socket",
                daemon=True,
            )
            self.control_thread.start()
            self.media_keys.start()
            LOG.info("Bedtime audio service is ready; waiting for a session start")
            from .ui import run_ui

            run_ui(self)
        finally:
            self.close()

    def request_shutdown(self) -> None:
        self.stop_requested.set()

    def close(self) -> None:
        self.stop_requested.set()
        with self.state_lock:
            self.session = stop_session(self.session)
            self.paused = False
            # Do not stop audio during controller shutdown or restart. Audio
            # has an independent systemd unit and remains in its current state.
        if self.control_server is not None:
            if self.control_thread is not None and self.control_thread.is_alive():
                self.control_server.shutdown()
                self.control_thread.join(timeout=2)
            self.control_server.server_close()
            self.control_server = None
        self.media_keys.close()
        self.player.close()
        try:
            CONTROL_SOCKET.unlink(missing_ok=True)
        except OSError:
            LOG.warning("Could not remove control socket %s", CONTROL_SOCKET)

    def handle_request(self, request: Mapping[str, object]) -> dict[str, object]:
        action = request.get("action")
        if action == "status":
            with self.state_lock:
                return {"ok": True, "session": self._session_json()}
        if action == "start":
            return self._start_session(request)
        if action == "stop":
            self.handle_media_key("stop")
            return {"ok": True, "session": self._status_snapshot()}
        if action == "pause":
            self.handle_media_key("pause")
            return {"ok": True, "session": self._status_snapshot()}
        if action == "resume":
            self.handle_media_key("play")
            return {"ok": True, "session": self._status_snapshot()}
        if action == "volume":
            value = request.get("percent")
            if not isinstance(value, (int, float)):
                raise ValueError("volume percent must be a number")
            self.player.set_volume(float(value))
            return {"ok": True, "session": self._status_snapshot()}
        raise ValueError("action must be start, stop, status, pause, resume, or volume")

    def handle_media_key(self, action: str) -> None:
        """Apply a standard media-key action without creating/restarting a session."""
        with self.state_lock:
            if action in ("volume_up", "volume_down", "mute_toggle"):
                try:
                    if action == "mute_toggle":
                        self.player.toggle_mute()
                    else:
                        self.player.adjust_volume(5 if action == "volume_up" else -5)
                except PlayerError:
                    LOG.exception("Could not apply media volume action %s", action)
                return
            alarm_adjustments = {
                "alarm_left": -15,
                "alarm_right": 15,
                "alarm_up": 60,
                "alarm_down": -60,
            }
            if action in alarm_adjustments:
                self.adjust_alarm(alarm_adjustments[action])
                return
            if action == "mode":
                if self.session.phase not in (Phase.PLAYLIST, Phase.PINK_NOISE):
                    self.selected_choice = (
                        StartChoice.PINK_NOISE
                        if self.selected_choice is StartChoice.READINGS
                        else StartChoice.READINGS
                    )
                    self.last_ui_error = None
                return
            if action == "stop":
                self.session = stop_session(self.session)
                self.paused = False
                self.last_ui_error = None
                self._request_audio_stop()
                LOG.info("Playback stopped")
                return
            if action in ("play", "toggle") and self.session.phase in (Phase.IDLE, Phase.STOPPED):
                self.last_ui_error = None
                try:
                    self._start_session(
                        {
                            "alarm": f"{self.alarm_minutes // 60:02d}:{self.alarm_minutes % 60:02d}",
                            "mode": self.selected_choice.value,
                        }
                    )
                except (ValueError, ServiceError, PlayerError) as error:
                    self.last_ui_error = str(error)
                    LOG.error("Could not start playback: %s", error)
                return
            if self.session.phase not in (Phase.PLAYLIST, Phase.PINK_NOISE):
                return
            if action == "toggle":
                action = "pause" if not self.paused else "play"
            if action == "pause" and not self.paused:
                self.player.set_paused(True)
                self.paused = True
                self._save_persisted_session()
            elif action == "play" and self.paused:
                self.player.set_paused(False)
                self.paused = False
                self._save_persisted_session()

    def adjust_alarm(self, minutes: int) -> None:
        """Adjust the ready-screen alarm time, wrapping within a local day."""
        with self.state_lock:
            if self.session.phase in (Phase.PLAYLIST, Phase.PINK_NOISE):
                return
            self.alarm_minutes = (self.alarm_minutes + minutes) % (24 * 60)
            self.last_ui_error = None

    def service_tick(self) -> None:
        """Advance playback events and enforce the alarm cutoff once."""
        now = time.monotonic()
        if now >= self.audio_event_poll_after:
            self.audio_event_poll_after = now + 0.25
            try:
                self.player.poll_events()
            except PlayerError:
                if now - self.audio_poll_error_logged_at >= 10:
                    LOG.exception("Could not read events from the independent audio service")
                    self.audio_poll_error_logged_at = now
        self._process_player_events()
        self._enforce_alarm()
        if self.audio_stop_pending and time.monotonic() >= self.audio_stop_retry_after:
            with self.state_lock:
                self._request_audio_stop()

    def _request_audio_stop(self) -> None:
        self._save_persisted_session()
        try:
            self.player.stop()
        except PlayerError:
            self.audio_stop_pending = True
            self.audio_stop_retry_after = time.monotonic() + 1.0
            LOG.exception("Audio stop is pending; will retry when the audio service responds")
        else:
            self.audio_stop_pending = False
            self._clear_persisted_session()

    def _start_session(self, request: Mapping[str, object]) -> dict[str, object]:
        alarm_text = request.get("alarm")
        mode = request.get("mode", StartChoice.READINGS.value)
        if not isinstance(alarm_text, str):
            raise ValueError("start requires an alarm time in HH:MM format")
        try:
            choice = StartChoice(str(mode))
        except ValueError as error:
            raise ValueError("mode must be 'readings' or 'pink_noise'") from error

        with self.state_lock:
            now = datetime.now(self.timezone)
            next_session = begin_setup(self.session)
            next_session = set_alarm(next_session, now, alarm_text)
            next_session = start_session(next_session, now, choice, self.schedule)
            self.session = next_session
            self._save_persisted_session()
            self.player.set_paused(False)
            self.paused = False
            if self.session.phase is Phase.PLAYLIST:
                self._play_current_track()
            elif self.session.phase is Phase.PINK_NOISE:
                self._play_pink_noise()
            else:
                self._request_audio_stop()
            LOG.info(
                "Session started: mode=%s alarm=%s day=%s",
                choice.value,
                self.session.alarm_at.isoformat() if self.session.alarm_at else "none",
                self.session.schedule_day,
            )
            return {"ok": True, "session": self._session_json()}

    def _play_current_track(self) -> None:
        assert self.session.schedule_day is not None
        self._save_persisted_session()
        entry = self.schedule[str(self.session.schedule_day)][self.session.track_index]
        path = MEDIA_ROOT / str(entry["file"])
        try:
            if "start_seconds" in entry:
                self.player.play_track(
                    path,
                    start_seconds=float(entry["start_seconds"]),
                    duration_seconds=float(entry["duration_seconds"]),
                )
            else:
                self.player.play_track(path)
        except (OSError, PlayerError, ValueError) as error:
            LOG.error("Cannot play scheduled track %s: %s", path, error)
            self.session = stop_session(self.session)
            self.paused = False
            self._request_audio_stop()
            raise ServiceError(f"cannot play {path.name}: {error}") from error

    def _play_pink_noise(self) -> None:
        self._save_persisted_session()
        try:
            self.player.play_pink_noise(PINK_NOISE_FILE)
        except (OSError, PlayerError) as error:
            LOG.error("Cannot play pink noise %s: %s", PINK_NOISE_FILE, error)
            self.session = stop_session(self.session)
            self.paused = False
            self._request_audio_stop()
            raise ServiceError(f"cannot play pink noise: {error}") from error

    def _process_player_events(self) -> None:
        while True:
            try:
                event = self.player_events.get_nowait()
            except queue.Empty:
                return
            with self.state_lock:
                if event.kind == "error":
                    LOG.error("Playback error for %s: %s", event.path, event.message)
                    if self.session.phase is Phase.PINK_NOISE:
                        # Keep the pink-noise session visible if its native
                        # continuous-output backend reports an error.
                        continue
                    self.session = stop_session(self.session)
                    self.paused = False
                    self._request_audio_stop()
                    continue
                if event.kind != "completed" or self.session.phase is not Phase.PLAYLIST:
                    continue
                self.session = track_finished(self.session, datetime.now(self.timezone))
                if self.session.phase is Phase.PLAYLIST:
                    LOG.info(
                        "Reading track complete; starting track %d of %d",
                        self.session.track_index + 1,
                        self.session.track_count,
                    )
                    try:
                        self._play_current_track()
                    except ServiceError:
                        LOG.exception("Could not continue scheduled readings")
                elif self.session.phase is Phase.PINK_NOISE:
                    LOG.info("Daily readings complete; starting pink noise")
                    try:
                        self._play_pink_noise()
                    except ServiceError:
                        LOG.exception("Could not start pink noise after readings")
                else:
                    self._request_audio_stop()

    def _enforce_alarm(self) -> None:
        with self.state_lock:
            old_phase = self.session.phase
            self.session = advance_time(self.session, datetime.now(self.timezone))
            if old_phase in (Phase.PLAYLIST, Phase.PINK_NOISE) and self.session.phase is Phase.STOPPED:
                LOG.info("Alarm time reached; stopping audio")
                self.paused = False
                self._request_audio_stop()

    def _load_persisted_session(self) -> tuple[Session, bool]:
        """Restore a valid session and absolute alarm deadline after restart."""
        try:
            data = json.loads(SESSION_STATE_FILE.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("session state must be an object")
            alarm_value = data.get("alarm_at_utc")
            if not isinstance(alarm_value, str):
                raise ValueError("session state has no alarm deadline")
            alarm_at = datetime.fromisoformat(alarm_value)
            if alarm_at.tzinfo is None or alarm_at.utcoffset() is None:
                raise ValueError("saved alarm deadline is not timezone-aware")
            alarm_at = alarm_at.astimezone(self.timezone)
            self.alarm_minutes = int(data.get("alarm_minutes", self.alarm_minutes)) % (24 * 60)
            choice = StartChoice(data.get("start_choice", StartChoice.READINGS.value))
            self.selected_choice = choice
            self.paused = bool(data.get("paused", False))
            phase = Phase(data.get("phase", Phase.IDLE.value))
            if phase is Phase.STOPPED:
                return Session(phase=Phase.STOPPED, alarm_at=alarm_at, start_choice=choice), True
            if phase not in (Phase.PLAYLIST, Phase.PINK_NOISE):
                raise ValueError("saved session is not active")

            session = Session(phase=phase, alarm_at=alarm_at, start_choice=choice)
            if phase is Phase.PLAYLIST:
                day = data.get("schedule_day")
                index = int(data.get("track_index", 0))
                if not isinstance(day, int) or not 0 <= day < 366:
                    raise ValueError("saved playlist day is invalid")
                entries = self.schedule.get(str(day), [])
                if not entries or not 0 <= index < len(entries):
                    raise ValueError("saved playlist position is invalid")
                session = Session(
                    phase=Phase.PLAYLIST,
                    alarm_at=alarm_at,
                    start_choice=choice,
                    schedule_day=day,
                    track_index=index,
                    track_count=len(entries),
                )
            if advance_time(session, datetime.now(self.timezone)).phase is Phase.STOPPED:
                LOG.info("Saved session alarm has passed; stopping restored audio")
                return Session(phase=Phase.STOPPED, alarm_at=alarm_at, start_choice=choice), True
            LOG.info("Restored %s session with alarm deadline %s", phase.value, alarm_at.isoformat())
            return session, False
        except FileNotFoundError:
            return Session(), False
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
            LOG.error("Ignoring invalid saved session state: %s", error)
            return Session(), False

    def _save_persisted_session(self) -> None:
        if self.session.phase is Phase.IDLE or self.session.alarm_at is None:
            return
        document = {
            "phase": self.session.phase.value,
            "alarm_at_utc": self.session.alarm_at.astimezone(timezone.utc).isoformat(),
            "start_choice": self.session.start_choice.value if self.session.start_choice else None,
            "schedule_day": self.session.schedule_day,
            "track_index": self.session.track_index,
            "track_count": self.session.track_count,
            "alarm_minutes": self.alarm_minutes,
            "paused": self.paused,
        }
        temporary = SESSION_STATE_FILE.with_suffix(".tmp")
        try:
            with temporary.open("w", encoding="utf-8") as state_file:
                state_file.write(json.dumps(document) + "\n")
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary, SESSION_STATE_FILE)
            directory = os.open(SESSION_STATE_FILE.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            LOG.exception("Could not persist the active bedtime session")

    @staticmethod
    def _clear_persisted_session() -> None:
        try:
            SESSION_STATE_FILE.unlink(missing_ok=True)
        except OSError:
            LOG.exception("Could not clear completed bedtime session state")

    def _status_snapshot(self) -> dict[str, object]:
        with self.state_lock:
            return self._session_json()

    def _session_json(self) -> dict[str, object]:
        return {
            "phase": self.session.phase.value,
            "alarm_at": self.session.alarm_at.isoformat() if self.session.alarm_at else None,
            "start_choice": self.session.start_choice.value if self.session.start_choice else None,
            "schedule_day": self.session.schedule_day,
            "track_index": self.session.track_index,
            "track_count": self.session.track_count,
            "paused": self.paused,
            "alarm_time": f"{self.alarm_minutes // 60:02d}:{self.alarm_minutes % 60:02d}",
            "selected_mode": self.selected_choice.value,
            "error": self.last_ui_error,
            "audio_stop_pending": self.audio_stop_pending,
        }

    @staticmethod
    def _clear_stale_control_socket() -> None:
        if not CONTROL_SOCKET.exists():
            return
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(CONTROL_SOCKET))
        except ConnectionRefusedError:
            if not stat.S_ISSOCK(CONTROL_SOCKET.lstat().st_mode):
                raise ServiceError(f"control path is not a Unix socket: {CONTROL_SOCKET}")
            CONTROL_SOCKET.unlink()
        except OSError as error:
            raise ServiceError(f"cannot inspect control socket {CONTROL_SOCKET}: {error}") from error
        else:
            raise ServiceError(f"bedtime service control socket is already active: {CONTROL_SOCKET}")
        finally:
            probe.close()


def run_service() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    service = BedtimeService()
    signal.signal(signal.SIGTERM, lambda _signum, _frame: service.request_shutdown())
    signal.signal(signal.SIGINT, lambda _signum, _frame: service.request_shutdown())
    service.run()


def send_control_request(request: Mapping[str, object]) -> dict[str, object]:
    """Send one JSON request to the running service's protected Unix socket."""
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(3)
    try:
        connection.connect(str(CONTROL_SOCKET))
        connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
        stream = connection.makefile("rb")
        response_line = stream.readline(MAX_CONTROL_MESSAGE_BYTES + 1)
        stream.close()
    except OSError as error:
        raise ServiceError(f"cannot contact bedtime service at {CONTROL_SOCKET}: {error}") from error
    finally:
        connection.close()
    if not response_line or len(response_line) > MAX_CONTROL_MESSAGE_BYTES:
        raise ServiceError("bedtime service returned an invalid response")
    try:
        response = json.loads(response_line)
    except json.JSONDecodeError as error:
        raise ServiceError("bedtime service returned invalid JSON") from error
    if not isinstance(response, dict):
        raise ServiceError("bedtime service response must be an object")
    return response
