use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::path::Path;

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Settings {
    pub hour: u8,
    pub minute: u8,
    pub reading: bool,
}

impl Default for Settings {
    fn default() -> Self {
        Self {
            hour: 6,
            minute: 30,
            reading: true,
        }
    }
}

impl Settings {
    pub fn validate(&self) -> Result<()> {
        if !(1..=12).contains(&self.hour) || ![0, 15, 30, 45].contains(&self.minute) {
            bail!("invalid alarm time: hour must be 1..12 and minute 0, 15, 30, or 45");
        }
        Ok(())
    }
}

pub fn load(path: &Path) -> Result<Settings> {
    let file = match File::open(path) {
        Ok(file) => file,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(Settings::default())
        }
        Err(error) => return Err(error).context("opening settings"),
    };
    let mut bytes = Vec::with_capacity(4097);
    file.take(4097)
        .read_to_end(&mut bytes)
        .context("reading settings")?;
    if bytes.len() > 4096 {
        bail!("settings file exceeds 4096 bytes");
    }
    let settings: Settings = serde_json::from_slice(&bytes).context("parsing settings")?;
    settings.validate()?;
    Ok(settings)
}

/// Replace one complete validated settings document, then sync its directory.
/// A preexisting staging file is refused rather than overwritten.
pub fn save(path: &Path, settings: &Settings) -> Result<()> {
    settings.validate()?;
    let parent = path
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or(Path::new("."));
    let name = path.file_name().context("settings path needs a filename")?;
    let mut staging_name = name.to_os_string();
    staging_name.push(format!(".{}.tmp", std::process::id()));
    let staging = parent.join(staging_name);
    let bytes = serde_json::to_vec(settings)?;
    let mut created = false;
    let result = (|| -> Result<()> {
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&staging)
            .context("creating settings staging file")?;
        created = true;
        file.write_all(&bytes).context("writing settings")?;
        file.sync_all().context("syncing settings")?;
        fs::rename(&staging, path).context("replacing settings")?;
        File::open(parent)?
            .sync_all()
            .context("syncing settings directory")?;
        Ok(())
    })();
    // Only remove a file created by this operation; a preexisting staging
    // file must not be removed when create_new failed.
    if result.is_err() && created {
        let _ = fs::remove_file(&staging);
    }
    result
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicU64, Ordering};
    static SERIAL: AtomicU64 = AtomicU64::new(0);
    fn directory() -> std::path::PathBuf {
        let path = std::env::temp_dir().join(format!(
            "shush-settings-{}-{}",
            std::process::id(),
            SERIAL.fetch_add(1, Ordering::Relaxed)
        ));
        fs::create_dir(&path).unwrap();
        path
    }
    #[test]
    fn round_trip_and_invalid_document() {
        let dir = directory();
        let path = dir.join("settings.json");
        assert_eq!(load(&path).unwrap(), Settings::default());
        let settings = Settings {
            hour: 12,
            minute: 45,
            reading: false,
        };
        save(&path, &settings).unwrap();
        assert_eq!(load(&path).unwrap(), settings);
        fs::write(&path, b"{\"hour\":13,\"minute\":30,\"reading\":true}").unwrap();
        assert!(load(&path).is_err());
        fs::write(&path, b"{").unwrap();
        assert!(load(&path).is_err());
        fs::write(&path, vec![b' '; 4097]).unwrap();
        assert!(load(&path).is_err());
        fs::remove_dir_all(dir).unwrap();
    }
    #[test]
    fn invalid_save_preserves_previous_settings() {
        let dir = directory();
        let path = dir.join("settings.json");
        save(&path, &Settings::default()).unwrap();
        assert!(save(
            &path,
            &Settings {
                hour: 0,
                minute: 0,
                reading: false
            }
        )
        .is_err());
        assert_eq!(load(&path).unwrap(), Settings::default());
        fs::remove_dir_all(dir).unwrap();
    }
}
