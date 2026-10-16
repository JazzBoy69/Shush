# Shush appliance image

This Buildroot external tree builds a custom 64-bit Linux SD image for the Raspberry Pi 3 B+. It includes the release Rust application, reading audio, pink noise, voice prompts, Wi-Fi and MQTT clients, Home Assistant device discovery, and headless clock/network failure reporting. The R19U hardware-volume fix and startup announcements are retained. The verification manifest records checks for the delivered image, including any reused media evidence; hardware endurance requires separate testing.

The built [SD image](../../dist/shush-pi3.img), [checksum](../../dist/shush-pi3.img.sha256), and [verification manifest](../../dist/shush-pi3.json) are available in `dist/`, alongside the extracted kernel configuration and validation logs. Use this image directly or rebuild as described below. It selects the connected receiver and speakers automatically; no manual device configuration is required.

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
python3 scripts/build_appliance.py --work .appliance-build/pi3 --jobs 4
python3 scripts/build_appliance.py --work .appliance-build/pi3 --verify-only
```

New builds write `sdcard-candidate.img` and `shush-candidate.json` first. The builder publishes `sdcard.img` only after full verification, retaining the previous image, checksum, and manifest in an `output/images/previous-image-*` directory. Failed candidates leave the published image intact. Direct Buildroot invocations produce an unpublished candidate; use the Python builder to verify and publish it.

Private configuration installation validates and stages all replacements before changing the target. Missing configuration leaves the installed copy intact; failed replacement installation restores it. Successful replacements retain the old private directory under `output/.shush-private-backup-*`, outside the packaged filesystem. These recovery directories contain credentials and have owner-only access. Credential generation uses atomic file replacement.

The script creates files only and never writes a physical device. Repeated builds resync the Rust sources and lockfile before compilation. If the appliance profile changes, use a fresh `--work` directory; the builder refuses to reuse a differently configured target filesystem.

## Install

Use an SD card of at least 4 GB. Write `dist/shush-pi3.img` (or a rebuilt `sdcard.img`) using Raspberry Pi Imager's custom-image option, selecting the intended SD card. This replaces its contents. The [official Raspberry Pi guide](https://www.raspberrypi.com/documentation/computers/getting-started.html) covers installing custom images.

Connect the Edifier R19U USB speakers and HP OVU400102 receiver, insert the flashed card, and boot with the configured Wi-Fi available or wired Ethernet connected. No device identifiers, host-side enumeration, or package installation on the Pi are required.

At every boot, startup discovers the single input node owned by the `mceusb` driver and the single playback PCM owned by `snd-usb-audio`, following their sysfs device ancestry. Device numbers may change between boots. It waits up to 60 seconds for both nodes and their access permissions, then opens them once. Multiple compatible receiver nodes or playback devices are rejected as ambiguous. Runtime disconnects do not trigger automatic reopening or recovery.

The boot partition's `shush.conf` defaults both `INPUT_DEVICE` and `AUDIO_DEVICE` to `auto`. Optional explicit overrides remain available for diagnostic use: a persistent `/dev/input/by-id/…-event…` input path and `plughw:CARD=identifier,DEV=number` output. Overrides require editing the card on a host and booting again; ordinary installation requires no edits.

Networking uses IPv4 DHCP on `eth0` and `wlan0`. `shush-wifi.service` authenticates with WPA2 using the private configuration. Ethernet takes route priority over Wi-Fi. DNS and NTP run through systemd; DHCP-provided NTP is accepted, with configured public servers as fallback. Local time is America/Chicago. The application requires clock synchronization before accepting playback. If synchronization has not completed within three minutes, a separate service announces “Unable to set the clock. Check the network connection.” once through the detected USB speakers. This diagnostic does not require the receiver or a synchronized clock. Playback remains stopped; after correcting the connection, reboot to retry startup. MQTT uses separate workers and is not required for playback. Startup initializes the detected Edifier R19U hardware mixer to raw 999 and unmutes it.

### Wi-Fi startup requirements

The radio drivers are built into the kernel and can request firmware before the SD root filesystem is mounted. `linux.fragment` embeds `regulatory.db`, its signature, the BCM43455 Wi-Fi binary, CLM data and Pi 3 B+ board configuration, and the BCM4345C0 Bluetooth firmware names. `external.mk` supplies the target firmware directory and orders Linux after the firmware packages. Installing files only in the root filesystem is insufficient for these early requests. Keep both embedded firmware and filesystem firmware aliases intact.

The patch under `patches/linux/` adds `cfg80211_port_authorized` after successful PSK/SAE firmware handshake completion. WPA supplicant 2.11 and later wait for this notification; the unpatched driver can report association, then leave the supplicant waiting until it disconnects on an authentication timeout, preventing DHCP and clock synchronization. The patch is applied through `LINUX_POST_PATCH_HOOKS` on fresh builds. It adapts the [upstream Broadcom authorization fix](https://lkml.iu.edu/hypermail/linux/kernel/2609.1/20183.html) to the pinned kernel.

### MQTT and Home Assistant

The MQTT clients start in the application after clock synchronization and device startup. Flat systemd credentials named `mosquitto_sub`, `mosquitto_pub`, and `topic` are supplied under `$CREDENTIALS_DIRECTORY`; the application uses that directory directly as the clients' configuration directory. A clock-startup failure occurs before these MQTT workers run.

The configured topic prefix supplies `<prefix>/action`, `<prefix>/state`, and `<prefix>/availability`. Discovery uses retained QoS 1 device configuration at `homeassistant/device/<device_id>/config`, refreshed every 60 seconds and retried five seconds after a failed attempt. The availability publisher has a retained `offline` last will and publishes retained `online` alongside state updates. Sensors expire after 90 seconds without state. Discovery and network I/O run off the playback/control threads, and a discovery publication attempt is bounded to three seconds.

See the [Home Assistant setup and entity descriptions](../../README.md#home-assistant) for broker setup, playback automation, the current alarm-button label mismatch, topic identity, and reconnection behavior. The source's live-broker integration test checks retained discovery, non-retained command handling, playback updates, broker restart recovery, and offline availability. It tests the MQTT protocol with Mosquitto; it does not run a Home Assistant instance.

### Headless failure reports

On clock synchronization failure, `systemd-time-wait-sync.service` starts both the audible error service and `shush-network-report.service`. The latter runs as root, captures network/service/clock/kernel diagnostics without reading private configuration files, and writes `shush-network-report.txt` on the FAT boot partition. It takes two snapshots, with a 45-second pause after the first collection, and has a 180-second service timeout. Leave the appliance powered for three minutes after the prompt before retrieving the report from the card on a host. A later failure replaces the file; a successful boot does not delete an older report, so check its timestamps.

Report writes use a temporary file and rename. If the boot partition was read-only, the reporter remounts it writable only for saving and restores read-only afterward. The rest of the journal remains volatile.

There is no SSH server, root login, interactive console/getty, or debug shell. The UART is configured at 115200 baud for kernel and service diagnostics, with no login prompt. Service diagnostics are forwarded to that console and retained in a bounded volatile journal. Ordinary journal logs are not saved across reboot; the clock-failure report is the exception. USB autosuspend is disabled to keep the receiver and speakers continuously available. Actual Pi boot, device discovery, clock synchronization, audio output, and overnight operation require the hardware acceptance checks in the repository README.

## Storage and service limits

| Storage | Size | Runtime use |
| --- | --- | --- |
| Boot FAT partition | 64 MiB | Normally read-only firmware, kernel, boot configuration, and `shush.conf`; failure report is saved here |
| System ext4 partition | 3,072 MiB | Read-only application, libraries, and all media; mounted `ro,noload,noatime` |
| Settings ext4 partition | 64 MiB | Writable `/var/lib/shush`; wake time and reading option |
| `/var` tmpfs | 64 MiB | Volatile OS state, with the settings partition mounted beneath it |
| Journal | 16 MiB maximum | Volatile logs, with 4 MiB files and rate limiting |
| `/tmp` tmpfs | 32 MiB | Bounded temporary storage |

The playback process writes settings only and runs as the dedicated `shush` account with audio/input access and filesystem restrictions. The separate root diagnostic service can write the failure report to the boot partition. No swap is configured, application swap is prohibited, and the application uses no automatic restart. Essential systemd device, networking, DNS, clock, mount, and journal services remain; interactive administration and unrelated network services are omitted. The objective is preventing interruptions, not recovering playback after a process failure.

Application and media changes require a newly built and flashed image. Flashing overwrites the settings partition too; copy its `settings.json` from the old card beforehand if you want to retain the wake time and reading option.

## What image verification establishes

The builder automatically invokes `scripts/verify_appliance.py`. It checks the image hash and three-partition layout, compares each embedded partition with its generated filesystem image, and reads actual root/boot filesystem contents to validate the AArch64 executable, mount/service configuration, schedule, all 771 main voice prompts, the supplemental clock-failure prompt, reading files, pink noise, and boot files. It also checks that required early firmware bytes are embedded in the kernel and that the compiled Broadcom connection-completion function calls `cfg80211_port_authorized`. It reconstructs `Image` from the inspected kernel ELF and requires an exact hash match with the packaged kernel. It then executes the target application against target libraries under AArch64 QEMU user mode for `check` and full media validation. Three emulated processes validate separate groups of complete original playlists; together they cover all 366 original days and every referenced file and segment. Temporary schedule fixtures repeat each group to satisfy the unchanged application's 366-day schema. The original packaged schedule is independently checked for exact integrity. The manifest records each group's original days and validation log hash.

The verifier defaults to all checks. Its explicit `--structure-only` option validates the image and ARM asset loading without repeating MP3 decoding, and records full media validation as not run. Delivered maintenance builds can use structure verification and retain earlier media-validation evidence after confirming unchanged decoder, dependency, schedule, and asset inputs. The verifier records zero newly decoded playlists for such a run. The current Python builder still requires a full 366-playlist verification before its automatic publication step; `--structure-only` is a standalone verifier option, not a builder publication mode. Reused evidence in the delivered image manifest is recorded separately from the new structure check. Passing those checks establishes image structure, packaged asset integrity, and target executable/media loading. QEMU user mode does not boot the kernel or exercise Pi USB/audio hardware; the manifest keeps `hardware_boot_verified` false. Hardware reliability remains unverified until the actual appliance passes its boot and endurance tests.

The supplemental clock-failure recording uses the selected `am_michael` voice and pinned neural preparation environment. Reproduce it with `scripts/generate_clock_prompt.py --models PATH --output DIRECTORY`; its metadata is in `voice/clock-network-error.json`.
