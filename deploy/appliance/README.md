# Shush appliance image

This Buildroot external tree builds a custom 64-bit Linux SD image for the Raspberry Pi 3 B+. It includes the release Rust application, reading audio, pink noise, voice prompts, Wi-Fi and MQTT clients. The R19U hardware-volume fix and startup announcements are retained. The new network profile requires a fresh image build and hardware validation; historical image checks do not validate this updated profile.

The built [SD image](../../dist/shush-pi3.img), [checksum](../../dist/shush-pi3.img.sha256), and [verification manifest](../../dist/shush-image.json) are available in `dist/`, alongside the extracted kernel configuration and validation logs. Use this image directly or rebuild as described below. It selects the connected receiver and speakers automatically; no manual device configuration is required.

## Build on a Linux host

Use an Ubuntu/Debian host with the standard utilities required by the [Buildroot manual](https://buildroot.org/downloads/manual/manual.html#requirement), plus Python and curl for this builder:

```sh
sudo apt-get install build-essential python3 wget curl cpio unzip rsync bc patch file
python3 scripts/build_appliance.py
```

The selected hardware is Edifier R19U USB speakers and an HP OVU400102 eHome IR receiver, with Wi-Fi or wired Ethernet. The image includes USB audio and built-in eHome/MCEUSB infrared decoding; the receiver supplies decoded Linux input keycodes without a separate LIRC daemon.

The script checks required host tools, downloads Buildroot 2026.08, verifies its pinned archive SHA-256, and uses the external tree's checked dependency hashes. Buildroot supplies the cross-compilation toolchain, Rust build tools, kernel, firmware, and image-generation/verification tools. A full checkout with all media is required; preparation environments and archived voice banks are excluded from the target. Build and checkout paths must contain no whitespace. The build requires network access for source downloads and enough host storage for sources, toolchains, intermediate output, and the image.

Options:

| Option | Purpose |
| --- | --- |
| `--config PATH` | Optional device overrides; omit for automatic selection |
| `--work PATH` | Build directory; defaults to `.appliance-build` under the repository |
| `--jobs N` | Parallel build jobs, 1–32; defaults to 4 |
| `--host-tools PATH` | Prepend an additional host-tools directory to the build PATH |
| `--verify-only` | Verify the image already present under the selected work directory |
| `--private-config PATH` | Private network directory; defaults to Git-ignored `.local/appliance/` |

Before building, generate private network files with `scripts/configure_network.py` as described in the root README. Passwords are entered at hidden prompts. The private directory must contain `wpa_supplicant.conf`, `mqtt/mosquitto_sub`, `mqtt/mosquitto_pub`, and `mqtt/topic`. An existing complete private directory can be supplied using `--private-config`; keep it outside Git. Never commit or publish an image containing your credentials. Images and the private directory are ignored by Git. A fresh `--work` directory is required after this profile change.

The generated image stores Wi-Fi credentials under root-only `/etc/shush-private/`. The MQTT service drop-in uses `LoadCredential` to provide the MQTT files to the service account, and permits IP sockets for its clients. Boot configuration remains limited to device selection. To change credentials on the read-only appliance, rebuild and flash a replacement image.

With default paths, build output is in `.appliance-build/build.log`; the flashable file is `.appliance-build/output/images/sdcard.img`. The adjacent `sdcard.img.sha256` records its hash, and `shush-image.json` records image and validation details. For example:

```sh
python3 scripts/build_appliance.py --work /tmp/shush-appliance-work --jobs 4
python3 scripts/build_appliance.py --work /tmp/shush-appliance-work --verify-only
```

The script creates files only and never writes a physical device. Repeated builds resync the Rust sources and lockfile before compilation. If the appliance profile changes, use a fresh `--work` directory; the builder refuses to reuse a differently configured target filesystem.

## Install

Use an SD card of at least 4 GB. Write `dist/shush-pi3.img` (or a rebuilt `sdcard.img`) using Raspberry Pi Imager's custom-image option, selecting the intended SD card. This replaces its contents. The [official Raspberry Pi guide](https://www.raspberrypi.com/documentation/computers/getting-started.html) covers installing custom images.

Connect the Edifier R19U USB speakers and HP OVU400102 receiver, insert the flashed card, and boot with the configured Wi-Fi available or wired Ethernet connected. No device identifiers, host-side enumeration, or package installation on the Pi are required.

At every boot, startup discovers the single input node owned by the `mceusb` driver and the single playback PCM owned by `snd-usb-audio`, following their sysfs device ancestry. Device numbers may change between boots. It waits up to 60 seconds for both nodes and their access permissions, then opens them once. Multiple compatible receiver nodes or playback devices are rejected as ambiguous. Runtime disconnects do not trigger automatic reopening or recovery.

The boot partition's `shush.conf` defaults both `INPUT_DEVICE` and `AUDIO_DEVICE` to `auto`. Optional explicit overrides remain available for diagnostic use: a persistent `/dev/input/by-id/…-event…` input path and `plughw:CARD=identifier,DEV=number` output. Overrides require editing the card on a host and booting again; ordinary installation requires no edits.

Networking uses IPv4 DHCP on `eth0` and `wlan0`. `shush-wifi.service` authenticates with WPA2 using the private configuration. Ethernet takes route priority over Wi-Fi. DNS and NTP run through systemd; DHCP-provided NTP is accepted, with configured public servers as fallback. Local time is America/Chicago. The application waits for clock synchronization before accepting playback. MQTT uses separate workers and is not required for playback. Startup initializes the detected Edifier R19U hardware mixer to raw 999 and unmutes it.

There is no SSH server, root login, interactive console/getty, or debug shell. The UART is configured at 115200 baud for kernel and service diagnostics, with no login prompt. Service diagnostics are forwarded to that console and retained in a bounded volatile journal; they are not saved across reboot. USB autosuspend is disabled to keep the receiver and speakers continuously available. Actual Pi boot, device discovery, clock synchronization, audio output, and overnight operation require the hardware acceptance checks in the repository README.

## Storage and service limits

| Storage | Size | Runtime use |
| --- | --- | --- |
| Boot FAT partition | 64 MiB | Read-only firmware, kernel, boot configuration, and `shush.conf` |
| System ext4 partition | 3,072 MiB | Read-only application, libraries, and all media; mounted `ro,noload,noatime` |
| Settings ext4 partition | 64 MiB | Writable `/var/lib/shush`; wake time and reading option |
| `/var` tmpfs | 64 MiB | Volatile OS state, with the settings partition mounted beneath it |
| Journal | 16 MiB maximum | Volatile logs, with 4 MiB files and rate limiting |
| `/tmp` tmpfs | 32 MiB | Bounded temporary storage |

The playback process writes settings only. The service runs as the dedicated `shush` account with audio/input access and filesystem restrictions. No swap is configured, application swap is prohibited, and the application uses no automatic restart. Essential systemd device, networking, DNS, clock, mount, and journal services remain; interactive administration and unrelated network services are omitted. The objective is preventing interruptions, not recovering playback after a process failure.

Application and media changes require a newly built and flashed image. Flashing overwrites the settings partition too; copy its `settings.json` from the old card beforehand if you want to retain the wake time and reading option.

## What image verification establishes

The builder automatically invokes `scripts/verify_appliance.py`. It checks the image hash and three-partition layout, compares each embedded partition with its generated filesystem image, and reads actual root/boot filesystem contents to validate the AArch64 executable, mount/service configuration, schedule, all 771 voice prompts, reading files, pink noise, and boot files. It then executes the target application against target libraries under AArch64 QEMU user mode for `check` and full media validation. Three emulated processes validate separate groups of complete original playlists; together they cover all 366 original days and every referenced file and segment. Temporary schedule fixtures repeat each group to satisfy the unchanged application's 366-day schema. The original packaged schedule is independently checked for exact integrity. The manifest records each group's original days and validation log hash.

The verifier defaults to all checks. Its explicit `--structure-only` option validates the image and ARM asset loading without repeating MP3 decoding, and records full media validation as not run. Volume-only builds use structure verification and retain earlier media-validation evidence where the decoder and assets are unchanged. Passing those checks establishes image structure, packaged asset integrity, and target executable/media loading. QEMU user mode does not boot the kernel or exercise Pi USB/audio hardware; the manifest keeps `hardware_boot_verified` false. Hardware reliability remains unverified until the actual appliance passes its boot and endurance tests.
