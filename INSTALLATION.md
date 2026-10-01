# Raspberry Pi Installation

This guide installs Shush as a full-screen bedtime audio application on Raspberry Pi OS Lite (64-bit). The service starts at boot and shows the ready screen. Starting a session blacks out the display while playback and remote controls continue. The application has no settings file: configure the Pi's timezone and audio output in the operating system, and set the alarm for each session.

## 1. Write Raspberry Pi OS Lite to the microSD card

Do this on a Windows, macOS, or Linux computer with an SD-card reader. You need a microSD card large enough for the operating system and the media library. The checked-in `Audio/` directory is about 2.2 GB; use at least a 32 GB card to leave room for the OS and future updates.

1. Download and install [Raspberry Pi Imager](https://www.raspberrypi.com/software/) on your computer.
2. Insert the microSD card into the computer. Back up anything on it that you need to keep: writing the OS erases the selected card.
3. Open Raspberry Pi Imager and choose **Choose Device**. Select the exact Raspberry Pi model being used.
4. Choose **Choose OS**. Select **Raspberry Pi OS (other)**, then **Raspberry Pi OS Lite (64-bit)**. Do not select a Desktop edition; the application installs only the small X/Tk packages it needs for its own full-screen screen.
5. Choose **Choose Storage** and carefully select the microSD card. Verify its capacity and device name so you do not select a computer drive.
6. Select **Next** and choose **Edit Settings** or **OS Customisation** when Imager offers it:
   - Set a hostname such as `shush-pi`.
   - Create an administrator username and password. This is your OS login account, not the application's `bedtime-audio` service account.
   - Set the correct locale and timezone. The timezone can also be set later in the next section.
   - Configure Wi-Fi if the Pi will use Wi-Fi; otherwise plan to connect Ethernet for setup.
   - Enable SSH if you want to perform setup from another computer. This is optional when a display and keyboard are attached.
7. Confirm the erase/write prompts and wait for Imager to finish writing and verifying the card. Eject the card safely.

The official Raspberry Pi instructions have screenshots for [installing an OS with Imager](https://www.raspberrypi.com/documentation/computers/getting-started.html#install-using-imager). The Lite edition is the command-line OS image; this application adds only the packages needed for its own display.

### Put the application files on a USB drive

On the computer that has the Shush repository, plug in a USB drive and create a folder named `Shush-install` at the top level of the drive. Copy these items from the repository root into that folder, keeping the folder names as shown:

```text
Shush-install/
├── Audio/                    (copy the entire folder and its contents)
├── bedtime_audio/            (copy the entire folder and its contents)
├── Pink_Noise.wav
├── bedtime-audio.service
└── reading_schedule.json
```

Do not put another `Shush/` folder between `Shush-install/` and these items. The legacy browser files (`schedule.js`, `Shush_v2.js`, and `Shush.htm`) are not needed on the Pi. Safely eject the USB drive from the computer.

## 2. First boot and OS time zone

Insert the card into the Pi. Connect the large display to HDMI, a keyboard for initial setup, the intended audio output, the HID remote receiver, and Ethernet if available. Connect power and sign in using the administrator username and password created in Imager. If using SSH, connect to the hostname you chose (for example, `ssh <admin-user>@shush-pi.local`).

Update the OS, then reboot:

```sh
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

After it restarts, sign in again and set or confirm the OS timezone. Replace `Area/City` with the correct IANA timezone for the installation:

```sh
timedatectl list-timezones
sudo timedatectl set-timezone Area/City
timedatectl status
```

Confirm that the reported local time is correct. The application uses this OS time for the alarm cutoff and schedule selection.

## 3. Install system packages and configure audio

Install the audio player, Python runtime support, remote-input tools, and graphical UI packages:

```sh
sudo apt update
sudo apt install --no-install-recommends \
  mpv alsa-utils evtest python3-evdev python3-tk python3-venv \
  fonts-dejavu-core xserver-xorg xinit
```

If you want sound through the TV speakers over HDMI, connect the Pi to the TV's intended HDMI input and turn the TV on before configuring audio. Raspberry Pi OS normally routes sound to HDMI when a display is connected. On Lite, you can explicitly select the TV output with the OS menu:

```sh
sudo raspi-config
```

Choose **System Options** → **Audio** → the HDMI output connected to the TV (for example, HDMI 1), then select **Finish**. Menu labels can vary slightly by OS release. This is an OS-level choice; the application uses the OS default audio device and has no audio-device setting. For other outputs such as USB audio, choose that device in the same menu.

If the default audio test still plays through the wrong output, set ALSA's system-wide default to the HDMI card number shown by `aplay -l`. In the example below the TV's HDMI card is card 1; use the number shown for the HDMI card on this Pi:

```sh
sudo nano /etc/asound.conf
```

Add these lines (replace `1` if `aplay -l` shows a different card number for the TV's HDMI device):

```text
defaults.pcm.card 1
defaults.ctl.card 1
```

Save in nano with Ctrl+O, press Enter, then exit with Ctrl+X. This sets the OS-wide ALSA default; it does not add an audio-device option to Shush. The `defaults.*.card` settings use the numeric card index, so use the number from `aplay -l`. See ALSA's [system-wide default-device configuration](https://www.alsa-project.org/wiki/Setting_the_default_device).

Inspect and test the selected output:

```sh
aplay -l
speaker-test -c 2 -t wav
```

Stop `speaker-test` with Ctrl+C. Confirm that the test is audible from the TV speakers. If there is no sound, check that the TV is on the Pi's HDMI input with its volume up, then revisit the OS audio selection. If using an audio HAT or USB device instead, install its OS driver and select it as the system default before continuing. Raspberry Pi documents the [Lite/console audio selection steps](https://www.raspberrypi.com/documentation/computers/configuration.html) and [HDMI audio behavior](https://www.raspberrypi.com/documentation/computers/config_txt.html#hdmi-audio).

## 4. Create the service account and install the application

Create a dedicated unprivileged account and the application directories:

```sh
sudo useradd --system --home-dir /var/lib/bedtime-audio \
  --create-home --shell /usr/sbin/nologin bedtime-audio
sudo install -d -o root -g root -m 0755 /opt/bedtime-audio
sudo install -d -o root -g root -m 0755 /opt/bedtime-audio/data
sudo install -d -o root -g root -m 0755 /opt/bedtime-audio/bedtime_audio
```

Plug in the USB drive. To identify its partition, run:

```sh
lsblk -o NAME,SIZE,FSTYPE,LABEL,MOUNTPOINTS
```

Look for the new device whose size and label match your USB drive. Use its **partition** name (the indented row with a filesystem), not the main device row. For example, if the listing shows:

```text
NAME        SIZE FSTYPE LABEL      MOUNTPOINTS
sda        29.8G
└─sda1     29.8G exfat  SHUSH-USB
mmcblk0    29.7G
├─mmcblk0p1  512M vfat             /boot/firmware
└─mmcblk0p2 29.2G ext4             /
```

the USB partition is `/dev/sda1`. Your name may differ; use the one shown on your Pi. Then mount it read-only and check the folder:

```sh
sudo mkdir -p /mnt/shush-usb
sudo mount -o ro /dev/sda1 /mnt/shush-usb
ls /mnt/shush-usb/Shush-install
```

Replace `/dev/sda1` in the mount command with your USB partition name. The last command should list `Audio`, `bedtime_audio`, `Pink_Noise.wav`, `bedtime-audio.service`, and `reading_schedule.json`. If the USB is already mounted, use its displayed mount point and skip the `mount` command. If the folder does not appear, check the mount point and USB folder layout before continuing.

Copy the application, migrated schedule, and systemd unit from the USB drive:

```sh
sudo install -o root -g root -m 0644 /mnt/shush-usb/Shush-install/bedtime_audio/*.py \
  /opt/bedtime-audio/bedtime_audio/
sudo install -o root -g root -m 0644 /mnt/shush-usb/Shush-install/reading_schedule.json \
  /opt/bedtime-audio/data/reading_schedule.json
sudo install -o root -g root -m 0644 /mnt/shush-usb/Shush-install/bedtime-audio.service \
  /etc/systemd/system/bedtime-audio.service
sudo python3 -m venv --system-site-packages /opt/bedtime-audio/.venv
```

The virtual environment shares Raspberry Pi OS's `python3-evdev` and Tkinter packages. The service runs the system-installed MPV executable.

## 5. Install the audio files

The schedule expects all referenced reading files under `/var/lib/bedtime-audio/Audio/`, and pink noise at `/var/lib/bedtime-audio/Pink_Noise.wav`. Copy the media, then make it readable but not writable by the service account:

```sh
sudo install -d -o root -g bedtime-audio -m 0750 /var/lib/bedtime-audio/Audio
sudo cp -a /mnt/shush-usb/Shush-install/Audio/. /var/lib/bedtime-audio/Audio/
sudo chown -R root:bedtime-audio /var/lib/bedtime-audio/Audio
sudo find /var/lib/bedtime-audio/Audio -type d -exec chmod 0750 {} +
sudo find /var/lib/bedtime-audio/Audio -type f -exec chmod 0640 {} +
sudo install -o root -g bedtime-audio -m 0640 /mnt/shush-usb/Shush-install/Pink_Noise.wav \
  /var/lib/bedtime-audio/Pink_Noise.wav
```

The schedule is installed at `/opt/bedtime-audio/data/reading_schedule.json`; do not move or rename the media after installing it. If updating the schedule or media later, preserve these paths and permissions.

Before unmounting the USB drive, compare the number of reading files at the source and destination. The two numbers should match:

```sh
find /mnt/shush-usb/Shush-install/Audio -type f | wc -l
sudo -u bedtime-audio find /var/lib/bedtime-audio/Audio -type f | wc -l
```

The second command runs as the service account because the installed media directory is intentionally not accessible to ordinary users.

Then check that every file named in the installed schedule exists and is readable by the service account, and that the pink-noise file is readable too:

```sh
sudo -u bedtime-audio python3 - <<'PY'
import json
from pathlib import Path

schedule = json.loads(Path("/opt/bedtime-audio/data/reading_schedule.json").read_text())
media_root = Path("/var/lib/bedtime-audio/Audio")
missing = []
for entries in schedule["days"].values():
    for entry in entries:
        path = media_root / entry["file"]
        if not path.is_file() or not path.stat().st_mode & 0o444:
            missing.append(str(path))

pink_noise = Path("/var/lib/bedtime-audio/Pink_Noise.wav")
if not pink_noise.is_file() or not pink_noise.stat().st_mode & 0o444:
    missing.append(str(pink_noise))

if missing:
    print("Missing or unreadable files:")
    print("\\n".join(missing))
    raise SystemExit(1)
print("All scheduled audio files and pink noise exist and are readable.")
PY
```

If the counts differ or the check reports missing files, copy the audio folder again. This merges the USB files into the Pi's audio folder without deleting files already there, then reapplies the read-only permissions. Run the verification commands again afterward:

```sh
sudo cp -a /mnt/shush-usb/Shush-install/Audio/. /var/lib/bedtime-audio/Audio/
sudo chown -R root:bedtime-audio /var/lib/bedtime-audio/Audio
sudo find /var/lib/bedtime-audio/Audio -type d -exec chmod 0750 {} +
sudo find /var/lib/bedtime-audio/Audio -type f -exec chmod 0640 {} +
```

The application also checks for missing scheduled audio when the service starts. Complete the checks before enabling the service so a copy problem is caught during setup.

After copying all files, safely unmount the drive before removing it:

```sh
sudo umount /mnt/shush-usb
```

## 6. Grant access to the display and remote input

The systemd service runs as `bedtime-audio` on `/dev/tty1` and starts the local X server for the full-screen UI. Add the service account to the Pi's existing `input`, `video`, and `audio` groups so it can read the HID receiver and access the display and sound devices:

```sh
getent group input
getent group video
getent group audio
sudo usermod -aG input,video,audio bedtime-audio
```

Group changes take effect when the service starts again; restart the service after adding these groups.

Before starting the service, use `evtest` to identify the receiver and press each button. Confirm that the receiver emits Linux key events corresponding to Left, Right, Up, Down, Channel Up, Channel Down, OK, Play, Play/Pause, and Stop. The application currently maps those standard evdev codes. If this receiver reports different codes, update `bedtime_audio/key_listener.py` to match the observed codes before installing/reinstalling the package.

## 7. Check playback as the service account

Choose a file that exists in the installed reading directory and verify that MPV can play it using the OS default audio output:

```sh
sudo -u bedtime-audio mpv --no-config --no-terminal --no-video \
  --audio-display=no /var/lib/bedtime-audio/Audio/<known-track>.mp3
```

Replace `<known-track>.mp3` with an installed filename. Stop MPV after confirming sound. Resolve any audio-device or file-access issue before enabling the service.

If MPV reports `cannot find card '0'`, `Unknown PCM default`, or `couldn't open play stream`, the audio file was found but the OS did not provide a usable default playback device. The PipeWire/JACK messages can appear when those optional audio servers are not running; first check ALSA and the HDMI device:

```sh
aplay -l
aplay -L | grep sysdefault
ls -l /dev/snd
id bedtime-audio
```

With the TV connected to the Pi and powered on, Raspberry Pi OS normally selects its first HDMI output. If `aplay -l` shows no sound cards, use `sudo raspi-config` → **System Options** → **Audio** to select the TV's HDMI output, then reboot and check again. The HDMI device name in `aplay -L` depends on the Pi model; Raspberry Pi documents names such as `sysdefault:CARD=vc4hdmi0` and `sysdefault:CARD=vc4hdmi1` for different HDMI ports. If a sound card is listed but `/dev/snd` is restricted to a group that `bedtime-audio` is not in, add the service account to that existing group (commonly `audio`) and restart the service. Then retry MPV using its default output; do not add a device override to the application. See Raspberry Pi's [Lite audio instructions](https://www.raspberrypi.com/documentation/computers/os.html#play-audio-and-video-on-raspberry-pi-os-lite).

## 8. Enable the boot service

The repository's `bedtime-audio.service` starts the full-screen UI on tty1 and launches the application automatically at boot. It reserves tty1 for the UI and disables X network listening. MPV's control socket is created under `/run/bedtime-audio/` with access limited to the service account.

Load the unit, enable it for boot, and start it now:

```sh
sudo systemctl daemon-reload
sudo systemctl enable --now bedtime-audio.service
systemctl status bedtime-audio.service
systemctl is-enabled bedtime-audio.service
```

The display should show the ready screen. When a session starts, it should turn completely black. Audio playback and remote controls remain active. Stop or the alarm cutoff restores the ready screen.

If the UI does not appear or audio does not start, inspect the service log:

```sh
sudo journalctl -u bedtime-audio.service -b --no-pager
sudo journalctl -u bedtime-audio.service -f
```

Use `Ctrl+C` to stop following the live log. After correcting files, permissions, or the unit, restart the service with `sudo systemctl restart bedtime-audio.service`.

## 9. Use the remote

The service waits on the ready screen; there is no menu to navigate and no Enter key is needed.

- Left/Right adjusts the alarm by 15 minutes; Up/Down adjusts it by one hour.
- Channel Up/Down toggles the selection between **Alarm with playlist** and **Alarm without playlist**.
- Play starts the selected mode immediately.
- OK starts the selected mode from the ready screen and toggles playback between playing and paused during a session.
- Play/Pause toggles playback; Play resumes after a pause.
- Stop ends playback and restores the ready screen. It does not stop the boot service.
- The display is black during playback and pause. The remote remains usable while it is black.

There is no snooze function. To inspect or operate the service for administration, use systemd and the local CLI rather than adding application configuration:

```sh
sudo systemctl status bedtime-audio.service
sudo -u bedtime-audio /opt/bedtime-audio/.venv/bin/python -m bedtime_audio status
sudo -u bedtime-audio /opt/bedtime-audio/.venv/bin/python -m bedtime_audio stop
```

## Updating the installation

To update from a USB drive, repeat the USB mount steps above and recopy the changed Python files and unit, then restart the service:

```sh
sudo install -o root -g root -m 0644 /mnt/shush-usb/Shush-install/bedtime_audio/*.py \
  /opt/bedtime-audio/bedtime_audio/
sudo install -o root -g root -m 0644 /mnt/shush-usb/Shush-install/bedtime-audio.service \
  /etc/systemd/system/bedtime-audio.service
sudo systemctl daemon-reload
sudo systemctl restart bedtime-audio.service
sudo umount /mnt/shush-usb
```

If the schedule or media changed, install those files at their fixed paths and preserve the ownership and read permissions described above. No application settings file needs to be created.
