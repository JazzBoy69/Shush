# Shush alarm appliance implementation plan

Build an audio-only Raspberry Pi appliance in Rust, using the behavior in `reference/` as its starting point. Its reliability objective is uninterrupted correct operation. Restarting or recovering after failure does not satisfy that objective.

This plan records the agreed behavior and implementation. The target is a Raspberry Pi 3 B+ with USB speakers and an HID remote receiver. Use America/Chicago for US Central time, with network clock synchronization available. Preserve the reference reading schedule and its existing date anchor.

## Voice revision

The user rejected eSpeak NG and selected Michael, a male American English neural voice. The exact mode labels are now “With reading” and “Without reading”; canonical time messages remain “Your alarm is set for …” and “The current time is ….” Every actively generated prompt goes through explicit phonemization, the shared `scripts/stress.py` pass, and neural synthesis with corrected phonemes. `scripts/neural_pipeline.py` is used by both the complete bank generator and active previews. The pass has a pure `postprocess_stress(text, phonemes)` API and records its version, raw/corrected phonemes, tokens, and correction metadata.

The complete 771-prompt corpus uses `shush-phrase-stress-v7`. All nonmode stress rules remain unchanged from verified v3. Introductions receive secondary prominence and “set” is unstressed. With nonzero minutes, the hour becomes secondary, leading “oh” is unstressed, and compound minutes use secondary tens plus a primary final unit, including “one.” Plain minutes carry primary stress at their lexical syllable; thirteen through nineteen receive final-teen primary stress. At zero minutes, the hour remains primary and “o'clock” becomes secondary. WITH receives primary stress and reading secondary. Without retains secondary prominence and reading primary in a single continuous model utterance. V7 adds a 40 ms hold at the model-aligned word boundary within that connected utterance, with 2 ms edge fades. V5's split-word 200 ms pause was rejected; v6 removed the pause without implementing the promised beat. Error sentences keep “start” and “diagnostics” primary, with neutral first-syllable “cannot” at secondary prominence and diagnostics' internal secondary retained. Per-word role/correction metadata records changes.

V7 retains the same 770 stress-marker changes as v5: all 768 time prompts, the error indication, and With reading. All speech sounds are preserved and unknown text or audited phoneme drift is rejected. Active mode previews now target `voice-samples/stress-v7/modes/`; the two continuous-utterance samples and complete bank have been verified and installed. Earlier samples and audits remain references, with v5 explicitly archived as the rejected forced-pause approach. The raw audit carries the exact mode labels, with the earlier raw fixture preserved in `stress-audit-raw-v1-v3.json`. No pitch processing is involved. The inserted hold is checked in the actual WAV; the user accepted this delivery as passable. Keep the installed v7 voice revision.

The complete Michael v7 bank is installed in `voice/`. Both mode messages use single model utterances, with primary WITH retained and a 40 ms aligned hold added to Without reading. Both full manifests and all 771 actual WAV bytes/hashes matched. The 769 nonmode WAVs are byte-identical to v6 and retain their independent reproduction proofs; the modes were freshly synthesized twice in separate sessions and match the listening samples exactly. `voice/reproduction-verification.json` records lineage, source hashes, and passed staged/installed native checks. The bank uses 163,577,914 bytes (156.0 MiB) of resident 44,100 Hz PCM within its 192 MiB limit, with no clipped samples. Eight tests pass, including the actual WAV's hold duration, placement, and WITH stress snapshot.

All v7 generation and validation sessions have finished successfully. Prior banks remain in `voice-michael-v3/` through `voice-michael-v6/`; v5 is the rejected 200 ms pause reference and v6 has no added beat. eSpeak remains in `voice-espeak-baseline/`, and independent comparison copies remain in `voice-reproduction-v*/`. Earlier partial candidates are unused. Current/error/mode/wake use fresh seed-zero sessions with sorted inputs; full verification repeats all 771. Optional reuse validates complete proof, recognized independent-session recipe, environment, text/stress metadata and hashes before copying unchanged families. Active mode previews target v7; larger prior preview sets remain references. Historical comparisons require explicit replay. Preparation uses fixed two-thread CPU inference and deterministic computation; the Pi uses WAV assets only.

## Restore working playback baseline (active, authorized)

The user reports the three-partition volume-only image is still broken. Removing FTP did not restore operation. The startup mixer initialization is now removed too; its hardware behavior was never established. Restore playback first, then investigate low volume from a functioning baseline. Do not add diagnostics, FTP, writable audio or volume experiments to this baseline image. Keep the existing startup volume and remote behavior until the working baseline is confirmed. No full media decoding is needed: original src/media.rs, src/control.rs, Cargo.toml and Cargo.lock match the earlier working source hashes.

FTP and writable audio remain removed. Withdrawn FTP image is dist/withdrawn-ftp; the failed volume-only image will also be retained separately. The prior volume experiment and its startup call are removed from production. Remaining: source/binary/startup comparison to the earlier working version, build and publish a plain three-partition baseline image, then confirm operation on the physical Pi before addressing gain. Cause of complete silence remains unconfirmed.

## Automatic USB selection correction (completed)

User authorized removing manual device setup and requested an error review first. Reviewed Rust startup/control/audio/settings/media and appliance mounts, kernel drivers, service enablement and packaging. Existing 18 Rust and five packaging tests pass. No additional confirmed playback defect found in reviewed paths. The manual helper's requirement for a by-id input path was also inappropriate for selecting the kernel MCEUSB rc input node. Review found the standard rc6-mce keymap has no PgUp entry. User selected Info instead; KEY_INFO (358) is present in that map and is now the current-time command. Physical remote key delivery still needs hardware verification.

Default boot config uses auto for both devices. A bounded startup launcher follows sysfs driver ancestry to select exactly one MCEUSB input node and one USB audio playback PCM, waits for usable character nodes and ALSA control/udev permissions, then execs Rust. It runs as shush under existing restrictions, does not reopen devices, and refuses ambiguity. Device numbers are discovered afresh each boot and are not persisted. Optional diagnostic overrides remain; normal flashing requires no configuration edits. The old manual-only helper is removed. The Rust key mapping and input test now use Info (KEY_INFO 358).

Correction is complete: 18 Rust tests, strict Clippy, formatting, seven packaging tests, native/ARM release builds, final target ARM BusyBox discovery and full image verification passed. Fixtures cover renumbering, unrelated devices, absence, missing ALSA control nodes and ambiguity. Actual image checks confirmed the launcher bytes, executable mode 0755, service entry point, boot configuration, partitions, driver support, all assets and ARM application loading. All three full ARM media groups completed, covering every original day/file/segment. Native media decoding and voice generation were unchanged; earlier native full validation and reproducibility proofs remain applicable.

Published replacement: dist/shush-pi3.img, 3356491776 bytes, SHA-256 d2eb8be22ec0950eefd20dc0b8d17a2775a8cd7cd71a12eb61133a370e3f777c. The staged copy was checked against the build checksum, rehashed before publication, fsynced and atomically renamed. Its checksum, final manifest, kernel configuration and ARM logs accompany it in dist. Manifest records automatic device selection and KEY_INFO. No build or validation sessions remain active. Logs: /tmp/shush-info-native-build.log, /tmp/shush-info-arm-build.log, /tmp/shush-auto-info-image.log, /tmp/shush-auto-info-verification.log. The initial /tmp/shush-auto-device-image.log build was superseded when the user selected Info. Temporary guarded publisher is /tmp/shush-publish-auto-info.py. Physical Pi/USB/remote and overnight checks remain unverified.

## Appliance image completion

Implementation is authorized. Buildroot 2026.08 is the selected image builder (agent choice), targeting Pi 3 B+ AArch64 with systemd, ALSA, udev, wired DHCP and NTP. Boot and system/media partitions remain read-only; a 64 MiB ext4 partition contains saved settings only. /var and logs are bounded RAM storage. No application restart, watchdog reboot, swap, runtime package updates, TTS, desktop, or remote login service. Configuration is placed on the FAT boot partition for host-side editing. Root/media are packaged at build time; the Pi requires no manual package installation.

The appliance image is complete and verified. `dist/shush-pi3.img` is 3,356,491,776 bytes (3.13 GiB), SHA-256 `d2eb8be22ec0950eefd20dc0b8d17a2775a8cd7cd71a12eb61133a370e3f777c`. Its checksum, verification manifest, extracted kernel configuration, three ARM media validation logs, and native validation log are published under `dist/`. Both READMEs give direct image links and flashing instructions; ordinary installation requires no device configuration edits. No SD card was written and no physical Pi boot or USB audio/receiver operation is claimed; `hardware_boot_verified` remains false.

Final checks passed: 18 Rust tests, strict Clippy, formatting, native and AArch64 release builds, seven appliance packaging regression tests, Python/shell syntax checks, and complete native/ARM scheduled-media validation. The verifier compared the real embedded partitions, kernel options, AArch64 executable, mount/service configuration, original schedule, all 771 voice WAVs, all 1,189 packaged MP3 hashes, pink noise, and boot files. Three complete-playlist ARM validation partitions covered all 366 original days, all 1,055 referenced MP3s and every segment; only two files were shared across groups. Each preserved its original playlists, repeated solely to satisfy the unchanged 366-day schema. Their largest playlists were 22.4, 25.8 and 23.8 minutes. Native validation with Michael v7 resident peaked at 501,744 KiB (490.0 MiB) RSS, with no swaps; physical runtime memory still needs measurement.

Media preparation now uses bounded geometric capacity growth rather than exact reallocation on every MP3 frame. The new test verifies infrequent growth, the hard PCM ceiling, and rejection beyond the limit. Voice assets and playback semantics are unchanged. The final kernel contains built-in USB host, USB audio, HID/evdev, LAN78XX Ethernet, rc-core/MCEUSB/RC6/MCE-keyboard decoding and hardware RNG. Static systemd dependency verification passed with the actual factory-generated `/var` mount; the older host checker warned only about newer upstream systemd options. `/var` is bounded RAM storage, settings have their own writable filesystem, and no process restart or watchdog reboot is configured.

All build/validation sessions have completed. Output is `/tmp/shush-image-build`, source `/tmp/buildroot-2026.08`, downloads `/tmp/shush-image-downloads`. The device/Info correction logs are listed above; earlier media-allocation/native proof logs are `/tmp/shush-app-final.log`, `/tmp/shush-image-allocation-fix.log`, `/tmp/shush-image-verification-final.log`, and `/tmp/shush-native-media-final.log`. Host cpio was extracted into `/tmp/shush-host-tools/usr/bin`; no system packages were installed. Buildroot timezone dataset is `northamerica`, with local zone America/Chicago. Future builds should use the supplied builder with a fresh work directory. Do not repeat passed checks without a change, failure or unresolved concern.

## Implementation status

Implementation is authorized and the Rust application is implemented in `src/`. `README.md` contains build, deployment, controls, resource bounds, and hardware acceptance instructions. `deploy/` contains the service and device configuration template. `scripts/generate_neural_voice.py`, `scripts/stress.py`, and `voice/manifest.json` define the selected reproducible voice assets.

The runtime uses resident mono 44,100 Hz PCM for reading and noise, a preloaded voice bank, a separate preparation worker, and a dedicated ALSA output thread. The output thread owns deadline enforcement independently of HID input and settings persistence. Playlist buffers are retired to the control thread for cleanup. HID commands follow the established mapping; settings writes use validated, synced atomic replacement. The original elapsed-time schedule calculation and Chicago date anchor are retained.

Host validation has passed 18 behavior/resource tests, including native ALSA null-output deadline handling, strict Clippy checks, a release build, and exact regeneration of all 771 WAV prompts. All 1,055 referenced MP3 files and all scheduled segments have been decoded successfully; the image packages all 1,189 supplied MP3 files and verifies their integrity. Validation at the final 44,100 Hz output rate passed, including preparation of the largest real playlist. The longest playlist is 25.8 minutes and uses 130.4 MiB PCM; the fixed playlist budget is 192 MiB, and the current resident Michael voice bank uses 156.0 MiB within its separate 192 MiB limit.

The remaining external validation is on the actual Pi, USB speakers, and HID receiver. No physical hardware playback or overnight endurance result is claimed. Next steps are to deploy using `README.md`, validate the deployed media, exercise the remote, and measure overnight audio continuity and silence timing.

Verification commands are `cargo test --locked`, `cargo clippy --locked --all-targets -- -D warnings`, `cargo build --release --locked`, `target/release/shush check --root .`, `target/release/shush validate-media --root .`, and `/tmp/shush-neural-voice-venv/bin/python scripts/generate_neural_voice.py --output voice --verify`; `python3 scripts/test_stress.py` checks the complete stress policy and preparation guards. In this workspace, dependencies are cached with `CARGO_HOME=/tmp/shush-cargo`, allowing offline Cargo checks. The first 44,100 Hz validation found Psalm 119 exceeded the initial 96 MiB single-file limit. The single-file bound now matches the 192 MiB playlist bound, and native-rate MP3 decoding avoids an unnecessary copy. Final media validation completed successfully in 2 minutes 56 seconds, with a host peak resident size of 404.1 MiB while the earlier 132.6 MiB eSpeak bank was resident and the largest real playlist was prepared. That historical peak used the old bank. The latest native full validation with Michael v7 and bounded geometric allocation passed at 501,744 KiB (490.0 MiB) peak RSS with no swaps; the Michael bank itself uses 156.0 MiB PCM. No validation sessions remain active. A subsequent arithmetic correction uses wide intermediate values for 32-bit Pi portability; it does not change the validated host sample counts.

## Agreed behavior

- USB speakers provide playback and spoken configuration feedback. There is no screen.
- A remote control receiver emits HID keycodes. The command mapping is recorded below.
- The user selects either the complete daily reading playlist followed by pink noise, or pink noise immediately.
- Reading plays in full. The intended use places reading completion before the wake deadline; playback does not cut the playlist short.
- Pink noise continues until the selected wake time, then stops. Silence is the wake signal inherited from Shush.
- Changing the reading option announces the selected option.
- Up and down arrows adjust hours. Left and right arrows adjust minutes in 15-minute increments, with spoken indications of the selected time.
- Wake time has no AM/PM selection. Playback sessions never exceed 12 hours. The configured time remains in use until the user changes it.
- Starting playback from idle announces the configured wake time before reading or pink noise begins. Resuming paused playback does not reannounce it.
- Pause pauses audio and leaves the original wake deadline running. Resume continues reading from its paused position or resumes pink noise, with the deadline unchanged.
- While paused, the user may change settings and request the current time.
- The reading option and configured wake time persist across power-off.
- A command speaks the current time while idle or paused. It is disabled while reading or pink noise is playing.
- Configuration and its voice feedback are available while idle or paused. There are no spoken prompts or overlays while reading or pink noise is playing.
- Volume, mute, play, pause, and stop media commands work.
- The accept or OK button acts as play/pause. Stop cancels current playback and its wake deadline; it retains the configured wake time.
- There is no snooze.
- Voice messages are packaged as reproducible WAV assets; speech generation happens during preparation or the build, not during appliance operation.

## Remote key mapping

| Linux keycode | Command | Behavior |
| --- | --- | --- |
| KEY_OK | toggle | Play or pause |
| KEY_PLAYPAUSE | toggle | Play or pause |
| KEY_PLAY | play | Start or resume playback |
| KEY_PAUSE | pause | Pause playback |
| KEY_STOP | stop | Cancel playback and its wake deadline |
| KEY_VOLUMEUP | volume_up | Increase volume |
| KEY_VOLUMEDOWN | volume_down | Decrease volume |
| KEY_MUTE | mute_toggle | Toggle mute |
| KEY_CHANNELUP | mode | Toggle reading before noise or noise immediately |
| KEY_CHANNELDOWN | mode | Toggle reading before noise or noise immediately |
| KEY_LEFT | alarm_left | Decrease wake minutes by 15 |
| KEY_RIGHT | alarm_right | Increase wake minutes by 15 |
| KEY_UP | alarm_up | Increase wake hour |
| KEY_DOWN | alarm_down | Decrease wake hour |
| KEY_INFO | current_time | Speak current time while idle or paused |

Mode and wake-time changes are available while idle or paused, with voice feedback. Starting from idle announces wake time; resuming does not. Info requests the current time while idle or paused; it is disabled while reading or pink noise is playing.

## Reference material

`reference/Shush.htm` and `Shush.css` supply the original controls and visual layout. The appliance carries forward the relevant behavior through the remote and voice interface.

`reference/Shush_v2.js` contains reading playback, pink noise playback, and time checks. The Rust implementation will replace browser execution, DOM state, and browser audio with explicit appliance state and native audio output.

`reference/reading_schedule.json` contains all 366 schedule days and 1,118 track entries, including 125 entries with start and duration fields. It matches `reference/schedule.js`. The schedule anchor is local time at 03:00 on 5 April 2020. Reading audio is available in the repository at `Audio/`, and the pink noise asset is `Pink_Noise.wav` at the repository root.

## Proposed software structure

Use a small Rust application with a deterministic control core. Separate the rules for playback and deadlines from Linux input, clock, audio, and storage access so that those rules can be tested without hardware.

The control core owns the selected reading option, wake time, active playback session, playlist position, volume, and mute state. Represent idle, reading, pink noise, and paused playback explicitly. Define each remote command in each state before implementing it. Configuration commands and the current-time command are available while idle or paused. They cannot produce speech while reading or pink noise is playing. Pause retains playback position and the original wake deadline; resume continues audio without repeating the wake-time announcement.

Map HID events to application commands using the mapping below. Handle key press, release, and repeat deliberately so that holding OK cannot repeatedly start and stop playback. Keep input traffic bounded and prevent it from delaying audio work.

Use native Linux audio output. Select the audio backend for the USB speakers after confirming the Pi and audio device. Feed it from bounded buffers; avoid allocation, file access, decoding, logging, and blocking locks in the audio callback or equivalent output loop. Prepare reading audio ahead of consumption and load the pink noise loop before starting the session.

Resolve the selected 12-hour wake time to its next occurrence within the 12-hour operating window, without an AM/PM setting. Define midnight, timezone, daylight-saving changes, and clock correction behavior explicitly. A delayed control iteration must still recognize a reached deadline instead of relying on equality with one particular minute. Freeze the chosen daily playlist for the session so that crossing a schedule boundary cannot change its remaining tracks.

## Reproducible voice assets

Maintain a manifest of prompt text, filenames, voice source, generator version, and audio settings. Pin the generation environment and use a deterministic source or retain fixed source recordings if the chosen speech engine cannot reproduce identical output.

Produce WAV assets in the playback format, with consistent levels and no accidental leading or trailing gaps. Include reading-option announcements and the phrases needed to assemble selected-time, playback-start, and current-time messages. Validate that every supported time can be spoken from the packaged assets. Network access and a speech engine are unnecessary at runtime.

## Failure prevention

Identify causes of missed or incorrect silence deadlines, interrupted noise, incomplete reading, and unintended remote actions before choosing implementation details. Treat premature silence as an important failure because silence is also the intended wake signal.

- Validate the schedule, segment ranges, voice assets, required reading audio, clock setup, and audio configuration before starting a session.
- A failed playback preflight leaves the appliance idle and plays the packaged error indication. It does not announce a successfully started session.
- Bound memory, queues, file handles, and diagnostic output. Keep configuration writes and diagnostics outside the audio and timing paths.
- Review production paths and dependencies for panics, unchecked arithmetic, unbounded work, and blocking operations. Rust memory safety is only one part of the requirement.
- Use network clock synchronization and America/Chicago for local time. Keep reading and noise playback independent of network services.
- Plan storage layout to minimize routine writes and prevent logs from filling storage. Evaluate a read-only operating-system image and a small settings area.
- Select cooling, storage, clock hardware, and USB audio configuration against the agreed operating conditions.
- Establish measurable limits for deadline accuracy, audio gaps, resource use, and continuous operating duration. Verify them on the actual appliance.

Process restart policies and watchdog reboots are not the reliability strategy for this project. Validation must show that the playback session continues correctly within the specified operating conditions.

## Implementation sequence

1. Resolve control semantics, time-setting behavior, hardware, and operating conditions. Record a state and command table and measurable acceptance criteria.
2. Inspect the receiver and record its keycodes, repeat behavior, and device identity. Confirm the audio device and output format on the selected Pi.
3. Implement the Rust control core and test full-playlist transitions, deadline handling, remote commands, and suppression of speech during playback.
4. Prepare and validate the reading schedule, reading audio, pink noise loop, and reproducible voice assets.
5. Integrate HID input, native audio, clock handling, and saved settings. Bound resource use and keep nonessential work outside playback timing.
6. Package the appliance OS and application with only the required services, deliberate storage writes, and documented hardware setup.
7. Run hardware acceptance and endurance tests. Measure playback continuity, deadline accuracy, memory use, and behavior under repeated input, CPU and storage load, and the agreed network conditions.

## Remaining preparation

Device selection is automatic; verify the actual receiver emits the established keycodes during hardware acceptance. Hardware models are Edifier R19U USB speakers and HP OVU400102 eHome IR receiver. Speaker USB audio is confirmed by manufacturer documentation. The pinned kernel mceusb driver supports HP/eHome receivers and outputs evdev keys; preserve the agreed application keycode mapping. Explicit built-in rc-core/MCEUSB/RC6/MCE-keyboard decoding is now added to linux.fragment. The final kernel rebuild passed and the required built-in options were extracted from the generated kernel; final image generation, the ARM asset check, and full ARM media validation passed. Device identifiers and observed keys still need confirmation on the actual unit. The user confirmed wired Ethernet; keep wired DHCP/NTP and omit Wi-Fi services/drivers.

Image assembly and full verification are complete. Flash the published image, then test the physical Pi/USB/HID combination. Use the README's one-second deadline tolerance and seven consecutive overnight sessions as initial acceptance criteria; measure gaps, memory, temperature and load behavior. Software/asset checks do not establish physical hardware reliability.
