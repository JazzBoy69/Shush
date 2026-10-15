#!/usr/bin/env python3
"""Idle-only A2DP pairing agent. The alarm process independently gates all PCM."""
import asyncio
import os
from pathlib import Path
import signal
import socket
import stat
import time

DIRECTORY = Path("/run/shush-bluetooth")
PAIR_SECONDS = 120
HEARTBEAT_SECONDS = 3
AUDIO_UUIDS = {
    "0000110a-0000-1000-8000-00805f9b34fb",
    "0000110b-0000-1000-8000-00805f9b34fb",
    "0000110c-0000-1000-8000-00805f9b34fb",
    "0000110d-0000-1000-8000-00805f9b34fb",
    "0000110e-0000-1000-8000-00805f9b34fb",
}


class Policy:
    def __init__(self):
        self.idle = False
        self.enabled = False
        self.last_seen = float("-inf")
        self.pair_until = 0
        self.revision = 0

    def apply(self, message, now):
        self.expire(now)
        if message == b"idle":
            self.idle = True
            self.last_seen = now
        elif message == b"busy":
            self.idle = False
            self.enabled = False
            self.pair_until = 0
            self.last_seen = now
        elif message == b"stop":
            self.enabled = False
            self.pair_until = 0
        elif message == b"pair" and self.idle:
            self.enabled = True
            self.pair_until = now + PAIR_SECONDS
            self.revision += 1

    def expire(self, now):
        if now - self.last_seen >= HEARTBEAT_SECONDS:
            self.idle = False
            self.enabled = False
            self.pair_until = 0

    def audio_allowed(self, now):
        self.expire(now)
        return self.idle and self.enabled

    def pairing_allowed(self, now):
        return self.audio_allowed(now) and now < self.pair_until


def runtime_socket(directory=DIRECTORY):
    directory.mkdir(parents=True, exist_ok=True)
    fifo = directory / "audio.fifo"
    if not fifo.exists():
        os.mkfifo(fifo, 0o660)
    if not stat.S_ISFIFO(fifo.stat().st_mode):
        raise RuntimeError("Bluetooth PCM path is not a FIFO")
    path = directory / "control.sock"
    if path.exists():
        if not stat.S_ISSOCK(path.stat().st_mode):
            raise RuntimeError("Bluetooth control path is not a socket")
        path.unlink()
    control = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    control.bind(str(path))
    path.chmod(0o660)
    control.setblocking(False)
    return control


def notify_ready():
    address = os.environ.get("NOTIFY_SOCKET")
    if address:
        if address.startswith("@"):
            address = "\0" + address[1:]
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
            channel.sendto(b"READY=1", address)


async def main(stopping=None, directory=DIRECTORY):
    from dbus_next import BusType, DBusError, Variant
    from dbus_next.aio import MessageBus
    from dbus_next.service import ServiceInterface, method

    policy = Policy()
    control = runtime_socket(directory)
    notify_ready()  # The PCM FIFO is ready even if optional Bluetooth hardware is delayed.
    loop = asyncio.get_running_loop()
    if stopping is None:
        stopping = asyncio.Event()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(signum, stopping.set)

    async def read_commands():
        while True:
            message = await loop.sock_recv(control, 32)
            policy.apply(message, time.monotonic())

    commands = asyncio.create_task(read_commands())
    bus = None
    properties = None
    try:
        while not stopping.is_set():
            try:
                bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
                root = await bus.introspect("org.bluez", "/")
                manager = bus.get_proxy_object("org.bluez", "/", root).get_interface(
                    "org.freedesktop.DBus.ObjectManager")
                objects = await manager.call_get_managed_objects()
                adapters = [path for path, interfaces in objects.items() if "org.bluez.Adapter1" in interfaces]
                if len(adapters) != 1:
                    raise RuntimeError("Expected one Bluetooth adapter")
                tree = await bus.introspect("org.bluez", adapters[0])
                properties = bus.get_proxy_object("org.bluez", adapters[0], tree).get_interface(
                    "org.freedesktop.DBus.Properties")
                break
            except Exception as error:
                if bus is not None:
                    bus.disconnect()
                    bus = None
                print("shush-bluetooth: adapter not ready:", type(error).__name__, flush=True)
                try:
                    await asyncio.wait_for(stopping.wait(), 5)
                except asyncio.TimeoutError:
                    pass
        if properties is None or stopping.is_set():
            return

        async def set_property(name, signature, value):
            await asyncio.wait_for(properties.call_set("org.bluez.Adapter1", name, Variant(signature, value)), 3)

        def require_pairing():
            if not policy.pairing_allowed(time.monotonic()):
                raise DBusError("org.bluez.Error.Rejected", "Pairing is not enabled")

        class Agent(ServiceInterface):
            def __init__(self):
                super().__init__("org.bluez.Agent1")

            @method()
            def Release(self):
                pass

            @method()
            def Cancel(self):
                pass

            @method()
            def RequestPinCode(self, device: 'o') -> 's':
                require_pairing()
                return "0000"

            @method()
            def RequestPasskey(self, device: 'o') -> 'u':
                require_pairing()
                return 0

            @method()
            def DisplayPinCode(self, device: 'o', pincode: 's'):
                require_pairing()

            @method()
            def DisplayPasskey(self, device: 'o', passkey: 'u', entered: 'q'):
                require_pairing()

            @method()
            def RequestConfirmation(self, device: 'o', passkey: 'u'):
                require_pairing()

            @method()
            def RequestAuthorization(self, device: 'o'):
                require_pairing()

            @method()
            async def AuthorizeService(self, device: 'o', uuid: 's'):
                if not policy.audio_allowed(time.monotonic()) or uuid.lower() not in AUDIO_UUIDS:
                    raise DBusError("org.bluez.Error.Rejected", "Bluetooth audio is unavailable")
                tree = await bus.introspect("org.bluez", device)
                props = bus.get_proxy_object("org.bluez", device, tree).get_interface(
                    "org.freedesktop.DBus.Properties")
                trusted = await props.call_get("org.bluez.Device1", "Trusted")
                if not trusted.value:
                    require_pairing()
                    paired = await props.call_get("org.bluez.Device1", "Paired")
                    if paired.value:
                        await props.call_set("org.bluez.Device1", "Trusted", Variant("b", True))
                # Recheck after awaited D-Bus calls; an alarm may have started meanwhile.
                if not policy.audio_allowed(time.monotonic()):
                    raise DBusError("org.bluez.Error.Rejected", "Bluetooth audio is unavailable")

        path = "/org/shush/agent"
        bus.export(path, Agent())
        tree = await bus.introspect("org.bluez", "/org/bluez")
        agents = bus.get_proxy_object("org.bluez", "/org/bluez", tree).get_interface("org.bluez.AgentManager1")
        await agents.call_register_agent(path, "NoInputNoOutput")
        await agents.call_request_default_agent(path)
        await set_property("Powered", "b", False)
        await set_property("Alias", "s", "Shush")
        await set_property("DiscoverableTimeout", "u", PAIR_SECONDS)
        await set_property("PairableTimeout", "u", PAIR_SECONDS)
        previous = None
        revision = -1
        while not stopping.is_set():
            now = time.monotonic()
            enabled = policy.audio_allowed(now)
            pairing = policy.pairing_allowed(now)
            desired = (enabled, pairing)
            if previous != desired or revision != policy.revision:
                await set_property("Powered", "b", enabled)
                if enabled:
                    # These properties may reject writes while the adapter is powered off.
                    await set_property("Discoverable", "b", False)
                    await set_property("Pairable", "b", False)
                    if pairing:
                        await set_property("Pairable", "b", True)
                        await set_property("Discoverable", "b", True)
                previous = desired
                revision = policy.revision
            try:
                await asyncio.wait_for(stopping.wait(), 0.2)
            except asyncio.TimeoutError:
                pass
    finally:
        commands.cancel()
        control.close()
        if properties is not None:
            try:
                await asyncio.wait_for(properties.call_set("org.bluez.Adapter1", "Powered", Variant("b", False)), 3)
            except Exception:
                pass
        if bus is not None:
            bus.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
