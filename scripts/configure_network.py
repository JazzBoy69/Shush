#!/usr/bin/env python3
"""Write private appliance network files; passwords are prompted, never argv."""
import argparse
import getpass
import hashlib
import os
import tempfile
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]


def private_file(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    # mkstemp creates a root/user-only file before any secret bytes are written.
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def wifi_config(ssid, password, country):
    ssid_bytes = ssid.encode("utf-8")
    if not 1 <= len(ssid_bytes) <= 32:
        raise ValueError("Wi-Fi SSID must contain 1 to 32 bytes")
    if not (len(country) == 2 and country.isascii() and country.isalpha()):
        raise ValueError("Country must be a two-letter code")
    if not 8 <= len(password.encode("utf-8")) <= 63:
        raise ValueError("WPA2 passphrase must contain 8 to 63 bytes")
    psk = hashlib.pbkdf2_hmac("sha1", password.encode("utf-8"), ssid_bytes, 4096, 32).hex()
    return (f"country={country.upper()}\nupdate_config=0\nnetwork={{\n"
            f"    ssid={ssid_bytes.hex()}\n    psk={psk}\n    key_mgmt=WPA-PSK\n}}\n")


def mqtt_config(host, port, username, password):
    for value in (host, username, password):
        if not value or "\n" in value or "\r" in value or "\x00" in value:
            raise ValueError("MQTT connection values must be nonempty single lines")
    if not 1 <= port <= 65535:
        raise ValueError("Invalid MQTT port")
    return f"-h {host}\n-p {port}\n-u {username}\n-P {password}\n"


def topic_config(topic):
    if (not 1 <= len(topic) <= 128 or topic.endswith("/")
            or any(not (c.isascii() and (c.isalnum() or c in "/_-")) for c in topic)):
        raise ValueError("Invalid MQTT topic prefix")
    return topic + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wifi-ssid", required=True)
    parser.add_argument("--country", default="US")
    parser.add_argument("--mqtt-host", required=True)
    parser.add_argument("--mqtt-port", type=int, default=1883)
    parser.add_argument("--mqtt-user", required=True)
    parser.add_argument("--topic", default="shush")
    args = parser.parse_args()
    # Validate everything before writing any secret files.
    wifi = wifi_config(args.wifi_ssid, getpass.getpass("Wi-Fi password: "), args.country)
    mqtt = mqtt_config(args.mqtt_host, args.mqtt_port, args.mqtt_user,
                       getpass.getpass("MQTT password: "))
    topic = topic_config(args.topic)
    directory = PROJECT / ".local/appliance"
    private_file(directory / "wpa_supplicant.conf", wifi)
    for name in ("mosquitto_sub", "mosquitto_pub"):
        private_file(directory / "mqtt" / name, mqtt)
    private_file(directory / "mqtt/topic", topic)
    print("Private network configuration saved in ignored .local/appliance/")


if __name__ == "__main__":
    main()
