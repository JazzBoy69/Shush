//! R19U USB attenuation is separate from the application's volume control.
use alsa::mixer::{Mixer, SelemChannelId, SelemId};
use anyhow::{ensure, Context, Result};
use std::{fs, path::Path};

fn card_identifier(device: &str) -> Option<&str> {
    let arguments = device.strip_prefix("plughw:CARD=")?;
    let (card, pcm) = arguments.split_once(",DEV=")?;
    (!card.is_empty()
        && card.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_')
        && !pcm.is_empty()
        && pcm.bytes().all(|b| b.is_ascii_digit()))
    .then_some(card)
}

fn is_r19u(sysfs: &Path, card: &str) -> bool {
    // Resolve numeric or persistent ALSA card identifiers without assuming card 0.
    for number in 0..32 {
        let directory = sysfs.join(format!("card{number}"));
        let matches = card.parse::<usize>().ok() == Some(number)
            || fs::read_to_string(directory.join("id")).is_ok_and(|id| id.trim() == card);
        if !matches {
            continue;
        }
        let Ok(device) = fs::canonicalize(directory.join("device")) else {
            return false;
        };
        return device.ancestors().any(|ancestor| {
            fs::read_to_string(ancestor.join("product"))
                .is_ok_and(|product| product.trim().eq_ignore_ascii_case("EDIFIER R19U"))
        });
    }
    false
}

fn raw_unity(range: (i64, i64)) -> Option<i64> {
    // This device uses linear 0..999, despite reporting 0..+3.90 dB.
    // Selecting "0 dB" would select raw zero and silence the speaker.
    (range == (0, 999)).then_some(999)
}

pub fn initialize(device: &str) -> Result<()> {
    let Some(card) = card_identifier(device) else {
        return Ok(());
    };
    if !is_r19u(Path::new("/sys/class/sound"), card) {
        return Ok(());
    }
    let mixer = Mixer::new(&format!("hw:{card}"), true).context("opening R19U mixer")?;
    let pcm = mixer
        .find_selem(&SelemId::new("PCM", 0))
        .context("R19U PCM mixer control missing")?;
    ensure!(pcm.has_playback_volume(), "R19U PCM lacks playback volume");
    let target =
        raw_unity(pcm.get_playback_volume_range()).context("unrecognized R19U PCM volume range")?;
    pcm.set_playback_volume_all(target)
        .context("setting R19U raw hardware volume")?;
    if pcm.has_playback_switch() {
        pcm.set_playback_switch_all(1)
            .context("unmuting R19U hardware output")?;
    }
    for channel in [SelemChannelId::FrontLeft, SelemChannelId::FrontRight] {
        if pcm.has_playback_channel(channel) {
            ensure!(
                pcm.get_playback_volume(channel)? == target,
                "R19U hardware volume did not accept raw {target}"
            );
            if pcm.has_playback_switch() {
                ensure!(pcm.get_playback_switch(channel)? != 0, "R19U remains muted");
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;

    #[test]
    fn r19u_uses_raw_full_scale_without_decibel_conversion() {
        assert_eq!(raw_unity((0, 999)), Some(999));
        assert_eq!(raw_unity((-6400, 0)), None);
        assert_eq!(raw_unity((0, 100)), None);
    }

    #[test]
    fn only_explicit_valid_card_selection_can_open_a_mixer() {
        assert_eq!(card_identifier("plughw:CARD=2,DEV=0"), Some("2"));
        assert_eq!(card_identifier("plughw:CARD=R19U,DEV=0"), Some("R19U"));
        for device in ["default", "plughw:CARD=,DEV=0", "plughw:CARD=2\0,DEV=0"] {
            assert_eq!(card_identifier(device), None);
        }
    }

    #[test]
    fn selects_product_on_the_output_card_instead_of_shared_vendor_id() -> Result<()> {
        let root = std::env::temp_dir().join(format!("shush-r19u-test-{}", std::process::id()));
        fs::create_dir(&root)?;
        let result = (|| -> Result<()> {
            fs::create_dir(root.join("card2"))?;
            fs::create_dir(root.join("usb"))?;
            fs::create_dir(root.join("usb/interface"))?;
            fs::write(root.join("card2/id"), "Speaker\n")?;
            symlink(root.join("usb/interface"), root.join("card2/device"))?;
            fs::write(root.join("usb/product"), "EDIFIER R19U\n")?;
            assert!(is_r19u(&root, "2"));
            assert!(is_r19u(&root, "Speaker"));
            assert!(!is_r19u(&root, "0"));
            fs::write(root.join("usb/product"), "Different speaker\n")?;
            assert!(!is_r19u(&root, "2"));
            Ok(())
        })();
        fs::remove_dir_all(root)?;
        result
    }
}
