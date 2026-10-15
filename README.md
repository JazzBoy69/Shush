# Shush alarm appliance

Rust implementation of Shush for a Raspberry Pi 3 B+, Edifier R19U USB speakers, and an HP OVU400102 eHome IR receiver. There is no screen. The wake signal is the end of pink noise: silence at the next occurrence of the selected 12-hour time. For example, 6:30 started at 9 PM stops at 6:30 AM; started at 5 PM it stops at 6:30 PM.

The reading option plays the complete daily playlist before pink noise. The other option starts pink noise immediately. The reference schedule retains its 366-day cycle and America/Chicago anchor at April 5, 2020, 03:00. The intended use allows the reading to finish before wake time. There is no snooze.

The daily playlist is decoded into memory during silent startup, before commands are accepted. Play and repeated Stop/Play reuse that buffer. Daily refresh waits for the first idle opportunity after 3 a.m. Chicago time, with no announcement or Bluetooth session active and at least 300 ms for queued speaker audio to clear. No sound commands are processed during the refresh. Active and paused sessions retain their playlist; the next eligible idle refresh prepares the new day. The original elapsed-time schedule is unchanged, including its winter boundary at 2 a.m.; refresh still waits until 3 a.m. If refresh fails, the previous playlist remains available and a silent retry is attempted after 60 seconds.

The earlier Python controller is retained in `bedtime_audio/`, with its native player in `native/audio_player.cpp`. Its setup instructions are in [INSTALLATION.md](INSTALLATION.md), and its implementation plan is in [PLAN-python.md](PLAN-python.md).

## Controls

| Remote key | Action |
| --- | --- |
| OK / Play-Pause | Start, pause, or resume |
| Play / Pause | Start or resume / pause |
| Stop | Cancel playback and its wake deadline |
| Up / Down | Change wake hour |
| Left / Right | Change wake minute by 15 minutes |
| Channel Up / Down | Toggle reading before noise / immediate noise |
| Info | Speak the current time |
| Volume Up / Down / Mute | Adjust playback volume / toggle mute |

Settings and current-time speech work while idle or paused. During reading and noise, configuration and speech commands are ignored. Once startup is ready, the appliance speaks the current time. Starting from idle immediately announces the selected wake time while media loads; resuming does not. Pause preserves position and leaves the wake deadline running. Settings changes while paused apply to the next session; the current deadline remains fixed. Wake time and reading option persist across power-off.

The remote adjusts software volume from 0 to 100%; 100% passes the original PCM signal without amplification. Startup initializes the detected Edifier R19U hardware mixer to raw 999 and unmutes it; software volume starts at 50%. Volume and mute changes have no spoken feedback.

## Wi-Fi and MQTT

The appliance profile includes the Pi 3 B+ Wi-Fi driver and firmware, WPA2 authentication, and DHCP on `wlan0`, alongside wired Ethernet. Private configuration lives in Git-ignored `.local/appliance/`. The builder installs it into `/etc/shush-private/` with restricted permissions; credentials are never copied into the tracked overlay or boot partition. MQTT credentials are supplied to the `shush` service through systemd credentials.

To create or replace the private files, run this command and enter both passwords at the prompts:

```sh
python3 scripts/configure_network.py --wifi-ssid YOUR_SSID --country US --mqtt-host YOUR_BROKER --mqtt-user YOUR_USERNAME
```

Publish a plain action name to `shush/action`: `play`, `pause`, `resume`, `toggle`, `stop`, `volume_up`, `volume_down`, `mute_toggle`, `mode`, `alarm_left`, `alarm_right`, `alarm_up`, `alarm_down`, `current_time`, or `status`. `start` aliases `play`; `mute` aliases `mute_toggle`. Commands follow the same state rules as remote buttons. Send actions without the retained flag. MQTT 5 subscriptions reject retained actions, including retained messages sent while connected.

The appliance publishes retained JSON to `shush/state` when state changes, on a `status` request, and every 30 seconds. Fields are `phase` (`idle`, `starting`, `reading`, `noise`), `paused`, `wake_hour`, `wake_minute`, `reading`, `volume`, `muted`, and the current session's UTC `deadline` (or `null`). This is a state snapshot, not an acknowledgement or an availability indicator; check its receipt time if monitoring connectivity. Use `--topic PREFIX` when generating private configuration to replace `shush`.

MQTT I/O runs on separate workers with bounded queues. A broker outage does not cancel or restart playback. Clients reconnect independently; actions delayed in the local queue beyond two seconds are discarded. The appliance runs MQTT clients only, with no local broker. The Mosquitto clients read passwords from private configuration files rather than command arguments ([client documentation](https://mosquitto.org/man/mosquitto_sub-1.html)).

## Build and install the appliance image

The appliance uses a custom Buildroot 2026.08 Linux image for the Raspberry Pi 3 B+ in 64-bit mode. It packages the Rust application, reading audio, pink noise, and voice prompts. The current source profile includes Wi-Fi and MQTT clients alongside wired Ethernet, and retains the R19U hardware-volume fix and startup announcements. Build and flash a new image to install these source changes. Previous image-validation results apply to the earlier images; this network profile still needs an image build and hardware validation.

Use the built [SD image](dist/shush-pi3.img), with its [SHA-256 checksum](dist/shush-pi3.img.sha256) and [verification manifest](dist/shush-image.json). It includes the USB audio and eHome/MCEUSB receiver drivers. The image automatically selects the single MCEUSB receiver and USB speaker playback device at boot; no device identifiers need entering.

To rebuild on an Ubuntu Linux host, install the required tools:

```sh
sudo apt-get install build-essential python3 wget curl cpio unzip rsync bc patch file
```

Build with automatic device selection:

```sh
python3 scripts/build_appliance.py
```

The builder downloads the checksum-pinned Buildroot release and its build dependencies, cross-compiles the application, creates `.appliance-build/output/images/sdcard.img`, and runs the image verifier. It writes an image file only. It never flashes an SD card. See [the appliance build guide](deploy/appliance/README.md) for build options, storage layout, and validation details.

1. Write `dist/shush-pi3.img` (or a rebuilt `sdcard.img`) to an SD card using Raspberry Pi Imager's custom-image option. Select the intended card carefully: writing replaces its contents. The [official Raspberry Pi installation guide](https://www.raspberrypi.com/documentation/computers/getting-started.html) describes Imager and custom images.
2. Connect the Edifier R19U USB speakers and HP OVU400102 receiver, insert the card, and boot the Pi. Use configured Wi-Fi or wired Ethernet; both use IPv4 DHCP. The OS synchronizes time over the network and uses America/Chicago. Playback remains unavailable until the clock is synchronized and the devices are ready.

The image has no SSH server or interactive console login. Updating application or media requires building and flashing a replacement image. If retaining settings matters, copy `/var/lib/shush/settings.json` from the old card's third partition before overwriting it.

## Host development and validation

For development on Linux, install Rust 1.88 or newer, `pkg-config`, and the ALSA development package (`libasound2-dev` on Ubuntu/Debian). These tools are for the development host; the appliance builder supplies the target toolchain and runtime.

```sh
cargo build --release --locked
cargo test --locked
cargo run --locked -- check --root .
cargo run --locked -- validate-media --root .
```

`check` validates configuration and assets. `validate-media` fully decodes the scheduled media and checks its segment ranges. The latest native run passed all 1,055 referenced MP3s and 366 playlist checks with the selected Michael bank loaded, peaking at approximately 490 MiB RSS without swaps; this is a host measurement, not a Pi runtime measurement. WAV assets are tracked in Git, including `Pink_Noise.wav` and the complete `voice/` bank. MP3 reading recordings and disk images remain excluded. Reading recordings must be supplied locally in `Audio/`; the tracked generator and manifests reproduce the voice WAVs. The image builder packages these files and validates the AArch64 application against them.

## Playback and failure prevention

The complete selected playlist is decoded before the start announcement, then held as mono 16-bit 44,100 Hz PCM. Pink noise stays resident and repeats without reopening its file. The largest supplied playlist is about 25.8 minutes and uses about 130.4 MiB of PCM, within the fixed 192 MiB playlist budget. All voice prompts are also resident, using about 156.0 MiB within a separate 192 MiB limit. Playlist cleanup occurs on the control thread, so freeing a large buffer cannot interrupt audio output. Preparation runs on a separate worker, so the receiver remains responsive to Stop. If preparation fails or the complete reading cannot fit before the deadline, playback does not start and the error prompt plays.

Media preparation grows decoding buffers geometrically within the 192 MiB capacity limit, avoiding exact reallocation for every MP3 frame. A dedicated ALSA output thread uses fixed buffers and bounded command queues. It enforces the wake deadline independently of input handling and settings writes. Pause retains its sample position; the wake deadline also applies while paused. The negotiated ALSA buffer must not exceed 250 ms; commands and silence onset can be delayed by queued audio within this bound. Volume starts at 50 percent and changes in five-percent steps. Mute silences output without changing playback position or the deadline.

The reading day uses the original elapsed-time calculation from the Chicago date anchor, including its behavior across daylight-saving changes. Wake selection uses the next existing local hour/minute after the displayed bedtime. A missing spring-forward occurrence is skipped; a local time already passed at bedtime stays passed when fall-back repeats an hour. There is no 12-hour elapsed-duration limit. Playback failure is reported without restarting. Losing the HID receiver does not stop independent audio or its deadline; the device is not automatically reopened.

The appliance mounts its boot and system partitions read-only, keeps settings on a separate writable persistent partition, and bounds temporary storage and journal logs in RAM. It has no swap, SSH server, or login/getty service. The application service requests elevated scheduling priority and waits for OS clock synchronization. Essential device, networking, DNS, time, and logging services support playback; the application has no automatic restart. These measures and host tests do not establish hardware reliability.

## Voice assets

The user selected Michael, a male American English neural voice. Every active prompt is phonemized, processed through `scripts/stress.py`, and synthesized with explicit corrected phonemes. Exact mode labels are “With reading” and “Without reading”; time messages remain “Your alarm is set for …” and “The current time is ….” V7 retains primary WITH and secondary reading in that label. “Without reading” uses secondary without and primary reading in one continuous synthesized utterance. A 40 ms hold is added at the model-aligned word boundary, with 2 ms edge fades, inside the connected phrase. The earlier split-word 200 ms pause was rejected and remains archived. All 771 prompts are audited: 770 change in stress markers. Nonmode phrase-focus rules and the teen/neutral-cannot lexical choices remain unchanged. Unexpected forms are rejected. Manifests record phoneme inputs, tokens, word roles, corrections, pass version, and aligned hold timing. Natural rhythmic delivery requires listening; stress markers alone do not establish it.

Active mode previews target `voice-samples/stress-v7/modes/` through `scripts/preview_modes.py`. Generic representative previews also default to v7 through `scripts/preview_voice.py` or `scripts/preview_stress.py`; the larger v7 set has not been rendered. Earlier samples remain references, including the rejected v5 200 ms pause; historical comparison replay requires `--historical`. Preparation dependencies are described in `voice-samples/README.md`.

`voice/` contains the complete selected Michael v7 bank: 771 mono, 16-bit, 24,000 Hz WAV prompts covering reading options, an error indication, every quarter-hour wake setting, and every current-time minute. Both complete manifests and all 771 WAVs match byte-for-byte. All 769 nonmode WAVs are unchanged and retain their earlier independent reproduction proofs; the two updated mode WAVs were freshly rendered twice in independent mode sessions. `voice/reproduction-verification.json` records the comparison, proof lineage, and source hashes. The installed bank passes `shush check`; it has no clipped samples and uses 163,577,914 bytes of resident 44,100 Hz PCM. Full time phrases avoid runtime synthesis and gaps between spoken fragments.

After preparing the pinned neural environment and model files as described in `voice-samples/README.md`, generate into a staging directory and verify before installing:

```sh
/tmp/shush-neural-voice-venv/bin/python scripts/generate_neural_voice.py --output voice-candidate --voice am_michael
/tmp/shush-neural-voice-venv/bin/python scripts/generate_neural_voice.py --output voice-candidate --verify
# Verify the installed bank without overwriting it:
/tmp/shush-neural-voice-venv/bin/python scripts/generate_neural_voice.py --output voice --verify
python3 scripts/test_stress.py
```

Generation and verification use a fresh seed-zero model session for each current-time, error, mode, and wake-time family, with sorted filenames within that family. Verification rejects an incomplete, reordered, or duplicated corpus, regenerates every prompt in the recorded order and environment, and compares metadata and hashes. Optional `--reuse-current voice` copies only unchanged current-time assets after validating their full reproduction proof, recognized inference recipe, environment, text, stress metadata, and actual hashes. This lets mode wording change without resynthesizing 720 current-time prompts. Optional `--reuse-unchanged voice` applies the same proof and compatibility checks to the current/error/wake families and regenerates only the mode family. Full `--verify` always synthesizes all 771 prompts.

The generator rejects direct generation into `voice/`. The previous eSpeak bank is preserved in `voice-espeak-baseline/`; `scripts/generate_voice.py` reproduces that historical bank only and defaults to its separate directory. Previous Michael v3/v4/v5 banks remain in their `voice-michael-v*/` reference directories; v5 contains the rejected forced pause. Earlier partial candidate directories are unused. The appliance loads and resamples the WAV files before accepting commands, including on ARM. Neural synthesis and the stress pass run only on the preparation host; the Pi performs no runtime synthesis or voice-file reads during operation.

## Acceptance on the actual appliance

The implementation and host checks cannot establish uninterrupted operation of the USB audio, receiver, storage, and Pi. Hardware acceptance remains necessary; no physical Pi or USB speaker testing has been performed in this development workspace.

The daemon refuses to accept playback until `timedatectl` reports that the OS clock is synchronized.

Before relying on the appliance:

1. Confirm the image verifier passed, then confirm the actual Pi boots, discovers the USB receiver and speakers automatically, and synchronizes its clock. The verifier already runs complete media validation under AArch64 QEMU; actual playback still needs hardware checks.
2. Exercise every remote key, key holding/repeat, pause/resume during reading and noise, configuration while paused, cancel, persistent settings, and speech suppression during playback.
3. Record the speaker output through complete reading-to-noise sessions and the noise loop boundary. Measure discontinuities and compare silence onset with the selected deadline; use a one-second deadline tolerance as the initial acceptance limit.
4. Run at least seven consecutive overnight sessions, including midnight and the schedule boundary, while monitoring memory, CPU temperature, storage space, and ALSA errors. Exercise receiver traffic and representative CPU/storage load during playback.
5. Verify the actual operating image's behavior across local daylight-saving boundaries and network clock corrections. Review measurements before declaring the appliance ready.

Failure prevention is the objective. A process restart or reboot does not count as a passed playback session. The supplied service makes a failure visible rather than concealing it with automatic restarts.
