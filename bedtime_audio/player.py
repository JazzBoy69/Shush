"""Local MPV adapter for the bedtime audio application."""

from __future__ import annotations

import json
import logging
import queue
import shutil
import socket
import stat
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping


LOG = logging.getLogger(__name__)
MPV_SOCKET = Path("/run/bedtime-audio/mpv.sock")
COMMAND_TIMEOUT_SECONDS = 5.0
START_TIMEOUT_SECONDS = 8.0


class PlayerError(RuntimeError):
    """Raised when MPV cannot start or rejects a playback command."""


@dataclass(frozen=True)
class PlayerEvent:
    kind: str
    path: Path | None = None
    reason: str | None = None
    message: str | None = None


EventHandler = Callable[[PlayerEvent], None]


class MpvPlayer:
    """Control one MPV process through its private Unix-domain JSON IPC socket.

    The default socket is under systemd's runtime directory. The service unit
    should create `/run/bedtime-audio` with mode 0700 for the service account;
    MPV's IPC endpoint is intentionally kept off the network.
    """

    def __init__(
        self,
        event_handler: EventHandler | None = None,
        *,
        socket_path: Path = MPV_SOCKET,
        executable: str = "mpv",
    ) -> None:
        self._event_handler = event_handler
        self._socket_path = Path(socket_path)
        self._executable = executable
        self._process: subprocess.Popen[str] | None = None
        self._socket: socket.socket | None = None
        self._reader: threading.Thread | None = None
        self._stderr_reader: threading.Thread | None = None
        self._send_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[dict[str, object]]] = {}
        self._request_id = 0
        self._current_path: Path | None = None
        self._closing = threading.Event()

    def start(self) -> None:
        """Start MPV in idle mode and connect the event/command channel."""
        if self._process is not None:
            return
        executable = shutil.which(self._executable)
        if executable is None:
            raise PlayerError(f"MPV executable not found: {self._executable}")
        if not self._socket_path.parent.is_dir():
            raise PlayerError(
                f"MPV runtime directory does not exist: {self._socket_path.parent}; "
                "create it for the service account in the systemd unit"
            )
        if self._socket_path.exists():
            probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                probe.connect(str(self._socket_path))
            except ConnectionRefusedError:
                if not stat.S_ISSOCK(self._socket_path.lstat().st_mode):
                    raise PlayerError(f"MPV IPC path is not a socket: {self._socket_path}")
                self._socket_path.unlink()
            except OSError as error:
                raise PlayerError(f"Cannot inspect existing MPV socket {self._socket_path}: {error}") from error
            else:
                raise PlayerError(f"MPV IPC socket is already active: {self._socket_path}")
            finally:
                probe.close()

        self._closing.clear()
        self._process = subprocess.Popen(
            [
                executable,
                "--no-config",
                "--idle=yes",
                "--no-terminal",
                "--no-video",
                "--audio-display=no",
                f"--input-ipc-server={self._socket_path}",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            close_fds=True,
        )
        self._stderr_reader = threading.Thread(
            target=self._read_stderr,
            name="mpv-stderr",
            daemon=True,
        )
        self._stderr_reader.start()

        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                raise PlayerError(f"MPV exited during startup (code {self._process.returncode})")
            if self._socket_path.exists():
                try:
                    self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self._socket.connect(str(self._socket_path))
                    break
                except OSError:
                    if self._socket is not None:
                        self._socket.close()
                        self._socket = None
            time.sleep(0.05)
        else:
            self._terminate_process()
            raise PlayerError(f"MPV IPC socket did not become available: {self._socket_path}")

        self._reader = threading.Thread(
            target=self._read_ipc,
            name="mpv-ipc-reader",
            daemon=True,
        )
        self._reader.start()

    def play_track(
        self,
        path: Path,
        *,
        start_seconds: float | None = None,
        duration_seconds: float | None = None,
    ) -> None:
        """Play a complete file or the specified legacy clip range."""
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise PlayerError(f"audio track is not a file: {path}")
        if (start_seconds is None) != (duration_seconds is None):
            raise ValueError("start_seconds and duration_seconds must be provided together")
        options: dict[str, str] = {"loop-file": "no"}
        if start_seconds is not None and duration_seconds is not None:
            if start_seconds < 0 or duration_seconds <= 0:
                raise ValueError("clip start must be nonnegative and duration must be positive")
            options["start"] = str(start_seconds)
            options["end"] = str(start_seconds + duration_seconds)
        self._load_file(path, options)

    def play_pink_noise(self, path: Path) -> None:
        """Loop one pink-noise file indefinitely until stop() is called."""
        path = Path(path).resolve(strict=True)
        if not path.is_file():
            raise PlayerError(f"pink-noise source is not a file: {path}")
        self._load_file(path, {"loop-file": "inf"})

    def set_volume(self, percent: float) -> None:
        """Set MPV's software volume without changing the OS audio device."""
        if not 0 <= percent <= 100:
            raise ValueError("volume must be between 0 and 100")
        self._command(["set_property", "volume", float(percent)])

    def set_paused(self, paused: bool) -> None:
        self._command(["set_property", "pause", bool(paused)])

    def stop(self) -> None:
        """Stop current playback and return MPV to idle mode."""
        if self._process is None or self._process.poll() is not None:
            return
        self._command(["stop"])
        with self._state_lock:
            self._current_path = None

    def close(self) -> None:
        """Stop audio and shut down the MPV child process cleanly."""
        self._closing.set()
        process = self._process
        if process is None:
            return
        if process.poll() is None:
            try:
                self._command(["quit"])
            except (PlayerError, OSError):
                process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        if self._reader is not None:
            self._reader.join(timeout=2)
            self._reader = None
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        self._process = None
        self._remove_stale_socket()

    def _load_file(self, path: Path, options: Mapping[str, str]) -> None:
        if self._process is None or self._process.poll() is not None:
            raise PlayerError("MPV is not running; call start() first")
        with self._state_lock:
            self._current_path = path
        # The -1 insertion index is required when passing per-file options to
        # loadfile on current MPV versions.
        self._command(["loadfile", str(path), "replace", -1, dict(options)])

    def _command(self, command: list[object]) -> dict[str, object]:
        connection = self._socket
        if connection is None:
            raise PlayerError("MPV IPC is not connected")
        with self._send_lock:
            self._request_id += 1
            request_id = self._request_id
            response_queue: queue.Queue[dict[str, object]] = queue.Queue(maxsize=1)
            with self._state_lock:
                self._pending[request_id] = response_queue
            request = json.dumps({"command": command, "request_id": request_id}) + "\n"
            try:
                connection.sendall(request.encode("utf-8"))
                response = response_queue.get(timeout=COMMAND_TIMEOUT_SECONDS)
            except (OSError, queue.Empty) as error:
                raise PlayerError(f"MPV command failed: {command!r}") from error
            finally:
                with self._state_lock:
                    self._pending.pop(request_id, None)
        if response.get("error") != "success":
            raise PlayerError(f"MPV rejected command {command!r}: {response.get('error')}")
        return response

    def _read_ipc(self) -> None:
        connection = self._socket
        if connection is None:
            return
        stream = connection.makefile("r", encoding="utf-8", errors="replace")
        try:
            for line in stream:
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    LOG.warning("Ignoring malformed MPV IPC message")
                    continue
                request_id = message.get("request_id")
                if isinstance(request_id, int):
                    with self._state_lock:
                        response_queue = self._pending.get(request_id)
                    if response_queue is not None:
                        try:
                            response_queue.put_nowait(message)
                        except queue.Full:
                            LOG.warning("Ignoring duplicate MPV command response %s", request_id)
                    continue
                if message.get("event") == "end-file":
                    self._handle_end_file(message)
        except OSError:
            if not self._closing.is_set():
                LOG.exception("MPV IPC connection ended unexpectedly")
                self._emit(PlayerEvent(kind="error", message="MPV IPC connection ended"))
        finally:
            stream.close()

    def _handle_end_file(self, message: Mapping[str, object]) -> None:
        data = message.get("reason")
        reason = str(data) if data is not None else "unknown"
        with self._state_lock:
            path = self._current_path
        if reason == "eof":
            self._emit(PlayerEvent(kind="completed", path=path, reason=reason))
        elif reason == "error":
            detail = message.get("file_error", message.get("error", "MPV playback error"))
            self._emit(PlayerEvent(kind="error", path=path, reason=reason, message=str(detail)))

    def _read_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            message = line.rstrip()
            if message:
                LOG.info("mpv: %s", message)

    def _emit(self, event: PlayerEvent) -> None:
        if self._event_handler is None:
            return
        try:
            self._event_handler(event)
        except Exception:
            LOG.exception("Player event handler failed for %s", event.kind)

    def _terminate_process(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        self._process = None
        self._remove_stale_socket()

    def _remove_stale_socket(self) -> None:
        try:
            self._socket_path.unlink(missing_ok=True)
        except OSError:
            LOG.warning("Could not remove MPV socket %s", self._socket_path)

    def __enter__(self) -> MpvPlayer:
        self.start()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()
