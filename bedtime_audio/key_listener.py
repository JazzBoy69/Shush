"""Optional Linux media-key listener for evdev-compatible keyboards/remotes."""

from __future__ import annotations

import logging
import threading
from typing import Callable


LOG = logging.getLogger(__name__)
KeyHandler = Callable[[str], None]


class MediaKeyListener:
    """Forward standard Linux Play/Pause/Stop key presses to the service.

    The evdev dependency is optional at runtime. Devices must expose standard
    Linux key codes and be readable by the bedtime-audio service account.
    """

    def __init__(self, handler: KeyHandler) -> None:
        self._handler = handler
        self._devices: list[object] = []
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        try:
            from evdev import InputDevice, ecodes, list_devices
        except ImportError:
            LOG.warning("python3-evdev is unavailable; media-key controls are disabled")
            return

        key_codes = {
            ecodes.KEY_OK: "toggle",
            ecodes.KEY_PLAYPAUSE: "toggle",
            ecodes.KEY_PLAY: "play",
            ecodes.KEY_PAUSE: "pause",
            ecodes.KEY_STOP: "stop",
            ecodes.KEY_VOLUMEUP: "volume_up",
            ecodes.KEY_VOLUMEDOWN: "volume_down",
            ecodes.KEY_MUTE: "mute_toggle",
            ecodes.KEY_CHANNELUP: "mode",
            ecodes.KEY_CHANNELDOWN: "mode",
            ecodes.KEY_LEFT: "alarm_left",
            ecodes.KEY_RIGHT: "alarm_right",
            ecodes.KEY_UP: "alarm_up",
            ecodes.KEY_DOWN: "alarm_down",
        }
        try:
            paths = list_devices()
        except OSError as error:
            LOG.warning("Cannot enumerate Linux input devices: %s", error)
            return

        for path in paths:
            try:
                device = InputDevice(path)
                capabilities = device.capabilities()
                supported = set(capabilities.get(ecodes.EV_KEY, []))
                if not supported.intersection(key_codes):
                    device.close()
                    continue
            except OSError as error:
                LOG.info("Cannot read input device %s: %s", path, error)
                continue

            self._devices.append(device)
            LOG.info("Listening for media keys on %s (%s)", path, device.name)
            thread = threading.Thread(
                target=self._read_device,
                args=(device, key_codes),
                name=f"media-keys-{path.rsplit('/', 1)[-1]}",
                daemon=True,
            )
            self._threads.append(thread)
            thread.start()

        if not self._threads:
            LOG.info("No readable Linux input device with standard media keys was found")

    def close(self) -> None:
        for device in self._devices:
            try:
                device.close()
            except OSError:
                pass
        for thread in self._threads:
            thread.join(timeout=0.5)
        self._devices.clear()
        self._threads.clear()

    def _read_device(self, device: object, key_codes: dict[int, str]) -> None:
        try:
            for event in device.read_loop():
                if event.type != 1 or event.value != 1:
                    continue
                action = key_codes.get(event.code)
                if action is not None:
                    try:
                        self._handler(action)
                    except Exception:
                        LOG.exception("Could not apply media-key action %s", action)
        except OSError as error:
            LOG.info("Media-key device disconnected: %s", error)
