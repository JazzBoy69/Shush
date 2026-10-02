# Migrate an installed Pi to the native audio daemon

This procedure updates an existing Shush Pi without reinstalling Raspberry Pi OS. `bedtime-audio-player.service` runs the compiled C++ audio daemon directly; `bedtime-audio.service` runs the Python UI and session controller. Pink noise is streamed from one in-memory PCM buffer through one open ALSA stream at the WAV's sample rate. The controller saves session and alarm state separately.

## Plan a short one-time handoff

The currently installed version owns MPV inside the UI/controller process. Linux cannot transfer that live MPV process to the new service. Schedule the cutover for a time when Shush is idle, or when a brief audio interruption is acceptable. After migration, ordinary UI/controller restarts do not stop audio.

Do not reboot as part of this procedure. Keep the Pi physically/network disconnected except for the maintenance connection you need, and disconnect the network again when finished.

## 1. Prepare the update USB

Copy these repository items to the existing `Shush-install/` USB folder, preserving its structure:

```text
Shush-install/
├── bedtime_audio/                 (Python UI/controller package)
├── native/audio_player.cpp         (native audio daemon source)
├── bedtime-audio.service          (updated controller unit)
├── bedtime-audio-player.service   (new audio unit)
├── MIGRATION.md
└── INSTALLATION.md
```

The existing schedule and audio files do not need to be copied again unless they were changed. On the Pi, mount the USB drive read-only as in `INSTALLATION.md` and verify the files are present:

```sh
ls /mnt/shush-usb/Shush-install
```

## 2. Turn off automatic maintenance actions

Write an APT settings file so update jobs run only when you deliberately invoke them:

```sh
sudo tee /etc/apt/apt.conf.d/20auto-upgrades >/dev/null <<'EOF'
APT::Periodic::Enable "0";
APT::Periodic::Update-Package-Lists "0";
APT::Periodic::Download-Upgradeable-Packages "0";
APT::Periodic::AutocleanInterval "0";
Unattended-Upgrade::Automatic-Reboot "false";
EOF
sudo systemctl disable --now apt-daily.timer apt-daily-upgrade.timer
sudo systemctl mask apt-daily.timer apt-daily-upgrade.timer unattended-upgrades.service
sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target
```

The network is normally disconnected, and OS updates are a manual maintenance task. These settings also disable automatic suspend/hibernate requests. They do not prevent a power, hardware, or kernel failure.

## 3. Install the updated controller, native daemon, and unit files

Keep the old controller running until the file copy is complete. Back up the current package and unit so rollback is concrete, then copy the updated Python package, native source, and both units. Install the native build tools if they are not already installed:

```sh
sudo install -d -o root -g root -m 0700 /var/backups/shush-pre-audio-daemon
sudo cp -a /opt/bedtime-audio/bedtime_audio /var/backups/shush-pre-audio-daemon/
sudo cp -a /etc/systemd/system/bedtime-audio.service /var/backups/shush-pre-audio-daemon/
sudo apt update
sudo apt install --no-install-recommends g++ libasound2-dev libjson-c-dev pkg-config
sudo install -o root -g root -m 0644 /mnt/shush-usb/Shush-install/bedtime_audio/*.py \
  /opt/bedtime-audio/bedtime_audio/
sudo install -o root -g root -m 0644 /mnt/shush-usb/Shush-install/bedtime-audio.service \
  /etc/systemd/system/bedtime-audio.service
sudo install -o root -g root -m 0644 /mnt/shush-usb/Shush-install/bedtime-audio-player.service \
  /etc/systemd/system/bedtime-audio-player.service
sudo install -d -o root -g root -m 0755 /opt/bedtime-audio/native /opt/bedtime-audio/bin
sudo install -o root -g root -m 0644 /mnt/shush-usb/Shush-install/native/audio_player.cpp \
  /opt/bedtime-audio/native/audio_player.cpp
sudo g++ -std=c++17 -O2 -Wall -Wextra -Wpedantic \
  /opt/bedtime-audio/native/audio_player.cpp \
  -o /opt/bedtime-audio/bin/bedtime-audio-player \
  $(pkg-config --cflags --libs alsa json-c) -pthread
sudo systemctl daemon-reload
```

No Python package is used by the audio daemon. MPV remains installed for scheduled reading tracks; pink-noise looping uses the native ALSA stream.

## 4. Hand off playback while Shush is idle

Confirm the current controller is idle, then stop it. This is the one-time cutover; the old version explicitly stops its own MPV during shutdown.

```sh
sudo -u bedtime-audio /opt/bedtime-audio/.venv/bin/python -m bedtime_audio status
sudo systemctl stop bedtime-audio.service
```

Enable and start the independent audio daemon, then start the updated controller:

```sh
sudo systemctl enable bedtime-audio-player.service
sudo systemctl start bedtime-audio-player.service
sudo systemctl enable bedtime-audio.service
sudo systemctl start bedtime-audio.service
```

The controller unit wants the audio service, and both are enabled for boot. The controller does not own, stop, or restart the audio service. The native audio unit uses `Restart=no`; playback is not automatically relaunched after the audio daemon exits. The daemon restores the saved pink-noise state when it is explicitly started again.

## 5. Verify without rebooting

Check both units and their recent logs:

```sh
systemctl is-enabled bedtime-audio-player.service bedtime-audio.service
systemctl --no-pager --full status bedtime-audio-player.service bedtime-audio.service
sudo journalctl -u bedtime-audio-player.service -u bedtime-audio.service -n 80 --no-pager
```

Start a pink-noise session from the remote/UI or an administrator shell, then restart only the UI/controller service:

```sh
sudo -u bedtime-audio /opt/bedtime-audio/.venv/bin/python -m bedtime_audio start --alarm 06:30 --mode pink-noise
sudo systemctl restart bedtime-audio.service
sudo -u bedtime-audio /opt/bedtime-audio/.venv/bin/python -m bedtime_audio status
```

Audio and the active session should continue through that controller restart. Confirm `phase` remains `pink_noise` and `alarm_at` is unchanged in the status output. The restored deadline is enforced at its original time. Once satisfied, disconnect the maintenance network and unmount the USB drive:

```sh
sudo umount /mnt/shush-usb
```

## Roll back

If the new controller cannot communicate with the audio daemon, inspect both service logs before reverting. To restore the old single-service arrangement, run:

```sh
sudo systemctl disable --now bedtime-audio.service
sudo systemctl disable --now bedtime-audio-player.service
sudo cp -a /var/backups/shush-pre-audio-daemon/bedtime_audio/. /opt/bedtime-audio/bedtime_audio/
sudo cp -a /var/backups/shush-pre-audio-daemon/bedtime-audio.service /etc/systemd/system/bedtime-audio.service
sudo systemctl daemon-reload
sudo systemctl enable --now bedtime-audio.service
```

Do not run both controller versions at once. The extra `audio_control.py` and `audio_daemon.py` files can remain on disk; the restored old package does not import them.

The manual-update and no-suspend settings can remain in place after rollback. Re-enable automatic package timers only if you explicitly want OS maintenance outside your maintenance window.
