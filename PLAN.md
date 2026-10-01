# Raspberry Pi Bedtime Audio Application — Implementation Plan

## Goal

Replace the browser-based Shush implementation with a standalone Raspberry Pi application. The service starts at boot and waits at a ready UI. The user sets an alarm time, chooses whether the alarm runs with or without the reading playlist, then presses Play to begin. The reading playlist plays in order and transitions to looping pink noise. Playback stops at the alarm time. There is no snooze feature.

The application runs locally without a browser or network connection during normal playback, starts on boot, and reconciles its state after a restart. Treat `schedule.js` and `Shush_v2.js` as the legacy behavioral specification: preserve the useful user workflows and schedule semantics while omitting browser-specific power-management workarounds and snooze.

## Source behavior discovered

`schedule.js` defines `readingSchedule`, a map of day indexes to ordered audio entries. Its data is not a weekday schedule: `ScheduleDay()` computes whole elapsed 24-hour periods from local April 5, 2020 at 03:00. Use modulo 366 to match all 366 keys (0 through 365), making the complete schedule reachable while preserving the anchor and elapsed-day semantics. The first migration phase creates a structured JSON copy with `cycle_days: 366`; the legacy browser player continues to read the JavaScript object until the native player is implemented.

Most entries are filenames under `./Audio/`. Some are comma-delimited clip specifications such as `nwt_01_Ge_E_11.mp3, 0, 94`; the current player interprets these as filename, start offset in seconds, and duration in seconds, then advances when playback time passes `start + duration`. Preserve these clips and sequence positions in a structured representation, not as filenames containing commas.

`Shush_v2.js` currently starts readings on a user action (`Alarm()`), selects the current schedule index, and moves to pink noise after the final entry. `AlarmNo()` starts pink noise directly. Pink noise comes from `Pink_Noise.wav` and loops. The configured alarm time is only checked while pink noise mode is active, so it does not currently cut off the reading playlist; the Pi version must enforce the alarm cutoff during either phase. The current UI stores a 12-hour hour/minute without AM/PM; the Pi interface must use an unambiguous time and timezone. Browser wake-lock, Web Audio fallback, and visibility-change handling are browser/mobile workarounds, not behavior to reproduce on the Pi.

The browser exposes OS/browser media-session play, pause, and stop actions. The Pi UI and remote controls should preserve those actions, and add the selected playlist mode and alarm-time adjustments described below. The remote receiver presents HID input; consume the resulting Linux input events rather than adding an IR or GPIO protocol decoder. Map Stop to immediate stop, and never expose snooze.

## Platform and runtime assumptions

Use a small Python 3 service on Raspberry Pi OS, with a system audio backend such as MPV controlled through local IPC. Keep schedule logic independent of the player so timing behavior can be reasoned about and recovered without an audio device.

Target Raspberry Pi OS Lite (64-bit). Do not add an application configuration file or setup screen for static system settings. Use the OS timezone and default audio device. Install the application, migrated schedule, readings, and `Pink_Noise.wav` at fixed documented paths under `/opt/bedtime-audio` and `/var/lib/bedtime-audio`; keep those paths as application/package constants. Configure timezone and audio output through Raspberry Pi OS.

There is no automatic bedtime start in the source code. Preserve a user-started flow: set the alarm time, toggle the selected mode between playlist and direct pink noise, then press Play. The alarm time is per-start input and the hard stop for either mode; it is not persistent application configuration. Use local OS time with explicit 12/24-hour presentation and define the date for after-midnight starts consistently (typically the date on which bedtime began). If the Pi is connected to a TV and picture-off is desired, put it behind an optional display-control interface and implement only for a confirmed TV protocol; audio must work when display control is unavailable.

## Runtime behavior

1. On startup, load and validate the installed schedule, read local time from the OS, initialize audio using the OS default output and available controls, and enter `IDLE`.
2. The ready UI lets the user adjust the alarm time and select **Alarm with playlist** or **Alarm without playlist**. Channel Up/Down toggles the selected label. Play starts the selected mode immediately: playlist mode selects the current cycle day and begins at its first entry; no-playlist mode starts pink noise directly. Both choices share the same alarm cutoff.
3. Play entries sequentially. For a clip entry, seek to `start_seconds` and stop after `duration_seconds`; for a normal entry, play to media completion. Handle player completion and error events rather than polling browser-style time updates.
4. After the final entry, start `Pink_Noise.wav` as a continuous loop immediately. If the playlist runs to the alarm time, stop it and do not start noise.
5. At alarm time, stop and unload audio regardless of whether a reading or pink noise is playing. Return the UI to its ready state; the boot-started service continues waiting for the next Play command.
6. If the service restarts during an active session, recover the saved mode and alarm deadline only if valid. If persisted session state is absent or invalid, return to `IDLE` and require a fresh user start. Never resume or play past the alarm.
7. Recalculate transitions using timezone-aware datetimes. Handle daylight-saving changes, clock corrections, and overnight sessions explicitly; do not assume every local day is 24 elapsed hours.
8. Stop cleanly on service shutdown and ensure no audio process remains orphaned.

## Schedule migration

Create a one-time migration utility that converts the JavaScript map into JSON with explicit day index and entry fields, for example:

```json
{
  "0": [
    {"file": "nwt_51_Col_E_03.mp3"},
    {"file": "nwt_01_Ge_E_11.mp3", "start_seconds": 0, "duration_seconds": 94}
  ]
}
```

Migration requirements:

- Preserve all entry ordering and exact filenames.
- Parse the legacy `file, start, duration` string form; validate nonnegative numeric offsets and positive durations.
- Validate that the 366 schedule keys are contiguous from 0 through 365 and use a 366-day selector cycle so every entry is reachable.
- Check every referenced media file exists under the fixed installed media directory and report all missing files before enabling playback.
- Keep the source `schedule.js` unchanged; retain a generated JSON schedule and a reproducible migration command in the project.

## Controls and remote integration

- Provide the designed full-screen graphical UI for a large display used at bedtime. Keep the ready screen calm and readable from across the room: a dark, warm brown background and panel, warm muted cream text, subdued amber accents, and no bright white surfaces or flashing effects. Scale the alarm time and controls to the display. Show the alarm time, selected **Alarm with playlist / Alarm without playlist** mode button, plus Play, Play/Pause, and Stop buttons. The service starts at boot and waits at this ready screen; remote Play starts the selected mode directly without menu or Enter navigation. As soon as playback starts, cover the display in pure black and keep it black while playing or paused. Restore the ready screen after Stop or the alarm cutoff; audio and remote controls continue operating while the display is black.
- Use keyboard arrow keys to adjust the alarm time. On the remote, Channel Up/Down toggles the selected mode and updates the label between **Alarm with playlist** and **Alarm without playlist**.
- The remote's Play key immediately starts the selected mode. Play/Pause toggles playback after start. Stop ends playback and returns the UI to its ready state; it does not stop the always-running service or schedule a restart.
- The local CLI and private Unix socket are administration/diagnostic interfaces, not the designed user interface. Keep the socket off the network.
- Consume the HID receiver through Linux evdev. Map `KEY_CHANNELUP` and `KEY_CHANNELDOWN` to playlist-mode toggle; `KEY_PLAY` to start the selected mode; `KEY_PLAYPAUSE` to playback toggle; `KEY_STOP` to stop; and arrow key events to alarm-time adjustment. `KEY_PAUSE` pauses and `KEY_PLAY` resumes while playback is paused. Do not add an IR/GPIO decoder or substitute a text key such as `N` for the media-key action.
- Do not implement snooze controls, snooze timer/state, or legacy snooze UI.

## Suggested project structure

```text
bedtime-audio/
  bedtime-audio.service
  data/reading_schedule.json
  bedtime_audio/
    __init__.py
    __main__.py
    planner.py
    player.py
    key_listener.py
    ui.py
    service.py
  tools/migrate_schedule.py
```

The current implementation uses a directly runnable Python package rather than a built wheel: place the `bedtime_audio/` directory and `bedtime-audio.service` at `/opt/bedtime-audio/`, and place the schedule under `/opt/bedtime-audio/data/`. The full-screen native UI uses Tkinter and a local X server; keyboard and remote input use Raspberry Pi OS's `python3-evdev` package. Create the venv with system-site packages enabled so it can import these OS Python packages.

Keep the planner pure: given session state, schedule, and a timezone-aware current time, it returns `idle`, `playlist`, `pink_noise`, or `stopped`, along with the alarm deadline and playlist index/clip details. The user flow is `IDLE → BEDTIME_SETUP → (PLAYLIST or PINK_NOISE) → STOPPED`; playlist completion changes `PLAYLIST → PINK_NOISE`, and alarm time or explicit Stop ends either playback state. The service executes that plan and reacts to player/control events.

## Raspberry Pi OS and MPV setup instructions

Use Raspberry Pi OS Lite (64-bit) as the target. The application has no static settings file: set the OS timezone and audio route on the Pi, enter the alarm time for each session, and install media at the documented fixed paths.

### 1. Set the Pi's local time zone

On the Pi, list valid timezone names and set the one for the installation:

```sh
timedatectl list-timezones
sudo timedatectl set-timezone Area/City
timedatectl status
```

Replace `Area/City` with the intended IANA timezone. Verify the displayed local time before enabling the bedtime service; the planner reads OS local time.

### 2. Install MPV and identify the OS audio output

Install MPV and ALSA command-line tools from the Raspberry Pi OS repositories:

```sh
sudo apt update
sudo apt install --no-install-recommends mpv alsa-utils python3-evdev python3-tk xserver-xorg xinit
```

Configure the desired output at the OS level. On Raspberry Pi OS Desktop, select HDMI, headphone, or USB audio from the desktop audio control. On Lite, inspect ALSA devices and test the intended output before installing the service:

```sh
aplay -l
speaker-test -c 2 -t wav
```

Stop `speaker-test` with Ctrl+C. If an audio HAT or other non-default device is used, follow that device's OS driver/setup instructions and make it the system default. Do not add an audio-device selector to the app or pass a device override to MPV. Raspberry Pi OS documents its [audio output choices](https://www.raspberrypi.com/documentation/computers/configuration.html#change-audio-output) and notes that Lite is intended for headless audio use in its [audio documentation](https://www.raspberrypi.com/documentation/accessories/audio.html).

### 3. Install the application and media at fixed paths

- Create a dedicated service account and the fixed directories, then copy the application package and unit from the repository:

```sh
sudo useradd --system --home-dir /var/lib/bedtime-audio --create-home --shell /usr/sbin/nologin bedtime-audio
sudo install -d -o root -g root -m 0755 /opt/bedtime-audio/data
sudo install -d -o root -g root -m 0755 /opt/bedtime-audio/bedtime_audio
sudo install -d -o bedtime-audio -g bedtime-audio -m 0750 /var/lib/bedtime-audio/Audio
sudo install -o root -g root -m 0644 bedtime_audio/*.py /opt/bedtime-audio/bedtime_audio/
sudo install -o root -g root -m 0644 reading_schedule.json /opt/bedtime-audio/data/reading_schedule.json
sudo install -o root -g root -m 0644 bedtime-audio.service /etc/systemd/system/bedtime-audio.service
sudo cp -a Audio/. /var/lib/bedtime-audio/Audio/
sudo install -o bedtime-audio -g bedtime-audio -m 0644 Pink_Noise.wav /var/lib/bedtime-audio/Pink_Noise.wav
sudo chown -R bedtime-audio:bedtime-audio /var/lib/bedtime-audio/Audio
sudo chmod -R u=rwX,g=rX,o= /var/lib/bedtime-audio/Audio
sudo python3 -m venv --system-site-packages /opt/bedtime-audio/.venv
```

Run those commands from the repository root on the Pi (or adjust only the source side if the checkout is elsewhere). Keep application code owned by root and readable by the service; media is readable by the service account.

For keyboard and remote controls, ensure the `bedtime-audio` account can read the relevant `/dev/input/event*` device. On Raspberry Pi OS installations that assign these devices to the `input` group, add the service account to that existing group with `sudo usermod -aG input bedtime-audio`, then restart the service so systemd applies the group membership. Use `evtest` during setup to confirm the receiver's HID buttons arrive as the expected Linux `KEY_*` events, including Channel Up/Down, Play, Play/Pause, Stop, and arrows; adjust the evdev mapping if the receiver reports different codes.

- Install the migrated schedule with the application under `/opt/bedtime-audio/data/reading_schedule.json`.
- Install readings under `/var/lib/bedtime-audio/Audio/` and `Pink_Noise.wav` at `/var/lib/bedtime-audio/Pink_Noise.wav`.
- Create a dedicated unprivileged `bedtime-audio` account. Make the application and media readable by that account; keep the media read-only to the service.
- Ensure the service account can access the OS audio device. On Lite/ALSA, inspect `/dev/snd` ownership and add the service account to the device's existing access group if required.
- Before enabling the service, verify MPV can play a known installed track as the service account, using the same default output:

```sh
sudo -u bedtime-audio mpv --no-config --no-terminal --no-video --audio-display=no \
  /var/lib/bedtime-audio/Audio/<known-track>.mp3
```

### 4. Create the systemd service and private MPV IPC directory

The MPV adapter uses the local Unix socket `/run/bedtime-audio/mpv.sock`. MPV's JSON IPC is intended for local control and is explicitly not a secure network protocol; never expose it through TCP or a network socket. The directory must be accessible only to the service account. The service starts a local X server on `/dev/tty1` for its full-screen graphical UI, conflicts with the login getty on that console, and passes `-nolisten tcp` so X accepts no network connections.

In `bedtime-audio.service`, include these service settings and the final application entry point:

```ini
[Unit]
Description=Raspberry Pi bedtime audio
After=local-fs.target sound.target getty@tty1.service
Conflicts=getty@tty1.service

[Service]
User=bedtime-audio
Group=bedtime-audio
WorkingDirectory=/opt/bedtime-audio
ExecStart=/usr/bin/startx /opt/bedtime-audio/.venv/bin/python -m bedtime_audio service -- :0 vt1 -keeptty -nolisten tcp
Environment=TERM=linux
StandardInput=tty-force
StandardOutput=journal
StandardError=journal
TTYPath=/dev/tty1
TTYReset=yes
TTYVHangup=yes
TTYVTDisallocate=yes
Restart=on-failure
RestartSec=3
RuntimeDirectory=bedtime-audio
RuntimeDirectoryMode=0700

[Install]
WantedBy=multi-user.target
```

`RuntimeDirectory=bedtime-audio` makes systemd create `/run/bedtime-audio` owned by the service account before startup and remove it when the service stops. `RuntimeDirectoryMode=0700` prevents other accounts from connecting to MPV's IPC socket. Do not manually create this transient directory. This is the systemd [runtime-directory mechanism](https://www.freedesktop.org/software/systemd/man/latest/systemd.exec.html#RuntimeDirectory=).

The adapter must launch MPV with `--no-config`, `--no-terminal`, `--no-video`, `--audio-display=no`, and `--input-ipc-server=/run/bedtime-audio/mpv.sock`. `--no-config` avoids requiring MPV-specific app configuration; MPV uses the OS audio default. Its [JSON IPC](https://mpv.io/manual/stable/#json-ipc) supports local command/event exchange, and its [loadfile options](https://mpv.io/manual/stable/#list-of-input-commands) support per-track clip ranges and file-loop settings.

After installing the unit, `enable --now` starts it immediately and enables it to start automatically during normal boot through `multi-user.target`. Confirm the enabled state before rebooting:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now bedtime-audio.service
systemctl status bedtime-audio.service
systemctl is-enabled bedtime-audio.service
journalctl -u bedtime-audio.service -f
```

Use `journalctl` to diagnose missing media, audio-device access failures, MPV startup errors, and IPC problems. The alarm time remains a session input; do not add it to the unit or an application config file.

### 5. Start and control bedtime audio

The service starts at boot and waits on the ready UI. Set the alarm with the UI's arrow-key controls. Channel Up/Down selects whether the label reads **Alarm with playlist** or **Alarm without playlist**; press the remote's Play key to start that selection. Play/Pause toggles playback, and Stop ends playback and returns to the ready UI. The CLI below is for local administration and diagnostics, not the normal bedtime interaction:

```sh
sudo -u bedtime-audio /opt/bedtime-audio/.venv/bin/python -m bedtime_audio start --alarm 06:30
sudo -u bedtime-audio /opt/bedtime-audio/.venv/bin/python -m bedtime_audio status
sudo -u bedtime-audio /opt/bedtime-audio/.venv/bin/python -m bedtime_audio stop
```

To skip readings and start pink noise immediately from an administrative shell, add `--mode pink-noise` to the `start` command. Keep the local Unix control socket off the network.

## Implementation sequence

1. Convert and validate `readingSchedule`; preserve the anchor, use a 366-day cycle to reach keys 0 through 365, and retain ordering and clip ranges.
2. Implement the deterministic session state machine for idle/setup, playlist, pink noise, and stopped. Take timezone and audio output from Raspberry Pi OS; accept the alarm time only as a per-session user input. Define overnight/session-date and daylight-saving behavior.
3. Implement the player adapter for ordered local tracks, clip seek/duration, pink-noise looping, volume, completion/error events, and stop. Use the OS default audio output without an app-level device setting.
4. Implement the long-running service: startup reconciliation, alarm cutoff enforcement, clean shutdown, log output, and local status/control if required.
5. Implemented: the designed graphical UI and remote actions. Arrow keys adjust the alarm; Channel Up/Down toggles the playlist choice label; Play starts the selected mode; Play/Pause toggles playback; Stop stops playback and returns to ready UI.
6. Package and deploy as a `systemd` service using the setup instructions above. Do not require an app configuration file.
7. Verify deterministic schedule/clip conversion and planner behavior with a fake clock and mock player, then verify on Pi hardware: bedtime setup, both start choices, phase transitions, clip boundaries, noise looping, cutoff during both playlist and noise, restart recovery, DST/time changes, missing media, audio failure, media keys, and confirmed remote/display integrations if applicable.

## Current implementation status

- Completed: schedule JSON migration and migration utility; fixed 366-day selector; pure session planner; MPV playback adapter for full tracks, clip ranges, looping pink noise, volume, pause/resume, and stop; long-running service with alarm cutoff and private local Unix-socket controls; administrative CLI; systemd unit configured to start at boot; Raspberry Pi OS setup and install instructions.
- Completed: full-screen Tkinter UI with visible mode, Play, Play/Pause, and Stop buttons; Left/Right adjust minutes by 15; Up/Down adjust hours; Channel Up/Down toggles playlist mode; Play starts the selected mode; the display turns pure black during playback or pause and Stop/alarm cutoff restore the ready screen. The Linux input adapter maps keyboard arrows and remote events through evdev.
- Still needed: persistent session/restart recovery; confirming the receiver's HID-to-Linux key mapping and device permissions on the Pi; on-device UI, playback, and boot verification.
- No application settings file is used. The OS provides timezone and default audio output; alarm time is entered for each session. There is no snooze behavior.

## Acceptance criteria

- Normal operation requires no browser or network access.
- Schedule selection matches the legacy anchor and reaches all 366 schedule entries through the 366-day cycle.
- All daily tracks play in the specified order, and clip entries honor their start and duration values.
- Pink noise starts after playlist completion and loops until the configured alarm time.
- Alarm time stops audio even during a long playlist; no new audio begins after the cutoff.
- Restart behavior follows the documented policy and never plays past alarm time.
- Missing files, invalid schedule entries, and player/device failures are clearly logged and cannot silently produce an unintended session.
- Local controls and supported OS media keys work; any programmed remote is tied to confirmed hardware/protocol.
- There is no snooze button, snooze timer, snooze state, or snooze behavior.
- Installation, service management, and diagnostics are documented and usable on Raspberry Pi OS.

## Open decisions for implementation

- Verify which Linux evdev key codes the HID receiver emits for each remote button.
- Identify the TV model/protocol if picture-off control is required.
- Confirm whether startup recovery should resume a saved playlist position or return to `IDLE` when session state is unavailable.
