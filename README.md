# Shush alarm appliance

Rust implementation of Shush for a Raspberry Pi 3 B+, Edifier R19U USB speakers, and an HP OVU400102 eHome IR receiver. There is no screen. The wake signal is the end of pink noise: silence at the next occurrence of the selected 12-hour time. For example, 6:30 started at 9 PM stops at 6:30 AM; started at 5 PM it stops at 6:30 PM.

The reading option plays the complete daily playlist before pink noise. The other option starts pink noise immediately. The reference schedule retains its 366-day cycle and America/Chicago anchor at April 5, 2020, 03:00. The intended use allows the reading to finish before wake time. There is no snooze.

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

Settings and current-time speech work while idle or paused. During reading and noise, configuration and speech commands are ignored. Starting from idle announces the selected wake time; resuming does not. Pause preserves position and leaves the wake deadline running. Settings changes while paused apply to the next session; the current deadline remains fixed. Wake time and reading option persist across power-off.

The remote adjusts software volume from 0 to 100%; 100% passes the original PCM signal without amplification. Startup does not change the USB hardware mixer. Volume and mute changes have no spoken feedback. 

## Build and install the appliance image

The appliance uses a custom Buildroot 2026.08 Linux image for the Raspberry Pi 3 B+ in 64-bit mode. It packages the Rust application, all reading audio, pink noise, and voice prompts; no packages or speech engine need installing on the Pi. The SD image has passed partition, packaged-file integrity, driver and AArch64 asset-loading checks under QEMU. The preceding image worked with low volume; the FTP image produced no response and has been withdrawn. The startup USB mixer experiment is also withdrawn after the volume-only image remained unresponsive. The plain three-partition image has been rebuilt from the original working application source; its physical operation still needs confirmation. Hardware endurance remains unverified.

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
2. Connect the Edifier R19U USB speakers, HP OVU400102 receiver, and wired Ethernet, insert the card, and boot the Pi. Ethernet uses IPv4 DHCP; the OS synchronizes time over the network and uses America/Chicago. Playback remains unavailable until the clock is synchronized and the devices are ready.

The image has no SSH server or interactive console login. Updating application or media requires building and flashing a replacement image. If retaining settings matters, copy `/var/lib/shush/settings.json` from the old card's third partition before overwriting it.

## Host development and validation

For development on Linux, install Rust 1.88 or newer, `pkg-config`, and the ALSA development package (`libasound2-dev` on Ubuntu/Debian). These tools are for the development host; the appliance builder supplies the target toolchain and runtime.

```sh
cargo build --release --locked
cargo test --locked
cargo run --locked -- check --root .
cargo run --locked -- validate-media --root .
```

`check` validates configuration and assets. `validate-media` fully decodes the scheduled media and checks its segment ranges. The latest native run passed all 1,055 referenced MP3s and 366 playlist checks with the selected Michael bank loaded, peaking at approximately 490 MiB RSS without swaps; this is a host measurement, not a Pi runtime measurement. Audio and image assets are excluded from Git. Reading recordings must be supplied locally in `Audio/`, with `Pink_Noise.wav` at the root; generated voice WAVs are prepared using the tracked generator and manifests. The image builder packages these files and validates the AArch64 application against them.

## Playback and failure prevention

The complete selected playlist is decoded before the start announcement, then held as mono 16-bit 44,100 Hz PCM. Pink noise stays resident and repeats without reopening its file. The largest supplied playlist is about 25.8 minutes and uses about 130.4 MiB of PCM, within the fixed 192 MiB playlist budget. All voice prompts are also resident, using about 156.0 MiB within a separate 192 MiB limit. Playlist cleanup occurs on the control thread, so freeing a large buffer cannot interrupt audio output. Preparation runs on a separate worker, so the receiver remains responsive to Stop. If preparation fails or the complete reading cannot fit before the deadline, playback does not start and the error prompt plays.

Media preparation grows decoding buffers geometrically within the 192 MiB capacity limit, avoiding exact reallocation for every MP3 frame. A dedicated ALSA output thread uses fixed buffers and bounded command queues. It enforces the wake deadline independently of input handling and settings writes. Pause retains its sample position; the wake deadline also applies while paused. The negotiated ALSA buffer must not exceed 250 ms; commands and silence onset can be delayed by queued audio within this bound. Volume starts at 50 percent and changes in five-percent steps. Mute silences output without changing playback position or the deadline.

The reading day uses the original elapsed-time calculation from the Chicago date anchor, including its behavior across daylight-saving changes. A nonexistent spring-forward wake time, or a next matching time more than 12 elapsed hours away at fall-back, is refused rather than silently changing wake time. Playback failure is reported without restarting. Losing the HID receiver does not stop independent audio or its deadline; the device is not automatically reopened.

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
