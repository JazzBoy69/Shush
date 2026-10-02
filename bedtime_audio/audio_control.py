"""Private local client for the independent audio daemon."""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import socket
from pathlib import Path
from typing import Callable

LOG = logging.getLogger(__name__)


class PlayerError(RuntimeError):
    """Raised when the native audio daemon rejects or cannot receive a request."""


@dataclass(frozen=True)
class PlayerEvent:
    kind: str
    path: Path | None = None
    reason: str | None = None
    message: str | None = None


AUDIO_SOCKET = Path("/run/bedtime-audio-player/audio.sock")


class AudioClient:
    """Send playback commands without owning or stopping the audio process."""

    def __init__(self, event_handler: Callable[[PlayerEvent], None] | None = None) -> None:
        self.event_handler = event_handler

    def start(self) -> None:
        self._request({"action": "ping"})

    def close(self) -> None:
        """The controller must never own the audio daemon's lifetime."""

    def play_track(self, path: Path, *, start_seconds: float | None = None,
                   duration_seconds: float | None = None) -> None:
        self._request({"action": "play_track", "path": str(Path(path).resolve()),
                       "start_seconds": start_seconds, "duration_seconds": duration_seconds})

    def play_pink_noise(self, path: Path) -> None:
        self._request({"action": "pink_noise", "path": str(Path(path).resolve())})

    def set_volume(self, percent: float) -> None:
        self._request({"action": "volume", "percent": percent})

    def adjust_volume(self, delta: int) -> None:
        if delta not in (-5, 5):
            raise ValueError("volume adjustment must be -5 or 5")
        self._request({"action": "volume_step", "delta": delta})

    def toggle_mute(self) -> None:
        self._request({"action": "mute_toggle"})

    def set_paused(self, paused: bool) -> None:
        self._request({"action": "pause", "paused": paused})

    def stop(self) -> None:
        self._request({"action": "stop"})

    def poll_events(self) -> None:
        result = self._request({"action": "events"})
        if self.event_handler is None:
            return
        for item in result.get("events", []):
            path = Path(item["path"]) if item.get("path") else None
            self.event_handler(PlayerEvent(kind=str(item["kind"]), path=path,
                                           reason=item.get("reason"), message=item.get("message")))

    @staticmethod
    def _request(request: dict[str, object]) -> dict[str, object]:
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(12)
        raw = b""
        try:
            connection.connect(str(AUDIO_SOCKET))
            connection.sendall(json.dumps(request).encode("utf-8") + b"\n")
            stream = connection.makefile("rb")
            raw = stream.readline(65537)
            stream.close()
        except OSError as error:
            raise PlayerError(f"audio service unavailable at {AUDIO_SOCKET}: {error}") from error
        finally:
            connection.close()
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as error:
            raise PlayerError("audio service returned invalid JSON") from error
        if not isinstance(response, dict) or response.get("ok") is not True:
            raise PlayerError(str(response.get("error", "audio service request failed")))
        return response
