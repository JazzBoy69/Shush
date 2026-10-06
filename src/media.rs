//! Decode and validate before playback. The audio thread consumes only resident PCM.
use anyhow::{bail, ensure, Context, Result};
use serde::Deserialize;
use std::{
    collections::BTreeMap,
    fs::File,
    io::Read,
    path::{Component, Path},
    sync::Arc,
};
use symphonia::core::{
    audio::SampleBuffer, codecs::DecoderOptions, errors::Error as DecodeError,
    formats::FormatOptions, io::MediaSourceStream, meta::MetadataOptions, probe::Hint,
};

pub const RATE: u32 = 44_100;
pub const MAX_SAMPLES: usize = 192 * 1024 * 1024 / 2;
const MAX_FILE_SAMPLES: usize = MAX_SAMPLES;
pub type Samples = Arc<[i16]>;

#[derive(Clone, Deserialize, Debug)]
#[serde(deny_unknown_fields)]
pub struct Track {
    pub file: String,
    #[serde(default)]
    pub start_seconds: u32,
    pub duration_seconds: Option<u32>,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Schedule {
    pub version: u32,
    pub anchor_local: String,
    pub cycle_days: usize,
    pub days: BTreeMap<String, Vec<Track>>,
}
impl Schedule {
    pub fn load(root: &Path) -> Result<Self> {
        let mut bytes = Vec::new();
        File::open(root.join("reference/reading_schedule.json"))?
            .take(1024 * 1024 + 1)
            .read_to_end(&mut bytes)?;
        ensure!(bytes.len() <= 1024 * 1024, "schedule too large");
        let schedule: Self = serde_json::from_slice(&bytes)?;
        ensure!(
            schedule.version == 1
                && schedule.cycle_days == 366
                && schedule.anchor_local == "2020-04-05T03:00:00",
            "unsupported schedule metadata"
        );
        ensure!(schedule.days.len() == 366, "schedule must contain 366 days");
        for day in 0..366 {
            let tracks = schedule.day(day)?;
            ensure!(
                !tracks.is_empty() && tracks.len() <= 64,
                "invalid track count for day {day}"
            );
            for track in tracks {
                let file = Path::new(&track.file);
                ensure!(
                    file.components().count() == 1
                        && matches!(file.components().next(), Some(Component::Normal(_))),
                    "invalid audio filename"
                );
                ensure!(track.duration_seconds != Some(0), "empty segment");
                ensure!(
                    root.join("Audio").join(file).is_file(),
                    "missing {}",
                    track.file
                );
            }
        }
        Ok(schedule)
    }
    pub fn day(&self, day: usize) -> Result<&[Track]> {
        self.days
            .get(&day.to_string())
            .map(Vec::as_slice)
            .context("missing reading day")
    }
}
fn reserve(samples: &mut Vec<i16>, count: usize, cap: usize) -> Result<()> {
    let required = samples.len().checked_add(count);
    ensure!(
        required.is_some_and(|n| n <= cap),
        "decoded media exceeds memory budget"
    );
    let required = required.context("sample count overflow")?;
    if required > samples.capacity() {
        // Exact per-frame growth can repeatedly move the whole decoded file.
        // Grow geometrically, but never request capacity beyond the PCM budget.
        let capacity = samples
            .capacity()
            .saturating_mul(2)
            .max(64 * 1024)
            .max(required)
            .min(cap);
        samples
            .try_reserve_exact(capacity - samples.len())
            .context("reserving media memory")?;
    }
    Ok(())
}
fn resampled_len(input_len: usize, source_rate: u32) -> Result<usize> {
    ensure!(source_rate > 0, "invalid source rate");
    let frames = (input_len as u64)
        .checked_mul(RATE as u64)
        .context("sample count overflow")?
        / source_rate as u64;
    usize::try_from(frames).context("sample count exceeds address space")
}
fn mono_resample(input: &[i16], source_rate: u32) -> Result<Vec<i16>> {
    ensure!(
        source_rate > 0 && source_rate <= 192_000 && !input.is_empty(),
        "invalid sample rate or empty audio"
    );
    if source_rate == RATE {
        let mut copy = Vec::new();
        copy.try_reserve_exact(input.len())?;
        copy.extend_from_slice(input);
        return Ok(copy);
    }
    let len = resampled_len(input.len(), source_rate)?;
    ensure!(
        len > 0 && len <= MAX_FILE_SAMPLES,
        "resampled audio exceeds memory budget"
    );
    let mut output = Vec::new();
    output.try_reserve_exact(len)?;
    // Linear interpolation; runtime ALSA converts 44.1 kHz PCM to the USB device rate.
    for n in 0..len {
        let position = n as u64 * source_rate as u64;
        let i = (position / RATE as u64) as usize;
        let fraction = (position % RATE as u64) as i64;
        let a = *input.get(i).context("resampler position")? as i64;
        let b = input.get(i + 1).copied().unwrap_or(a as i16) as i64;
        output.push((a + (b - a) * fraction / RATE as i64) as i16);
    }
    Ok(output)
}
pub fn wav(path: &Path) -> Result<Samples> {
    let mut reader =
        hound::WavReader::open(path).with_context(|| format!("opening {}", path.display()))?;
    let spec = reader.spec();
    ensure!(
        spec.bits_per_sample == 16
            && spec.sample_format == hound::SampleFormat::Int
            && (1..=2).contains(&spec.channels),
        "WAV must be 16-bit mono or stereo"
    );
    let channels = spec.channels as usize;
    let mut mono = Vec::new();
    ensure!(
        reader.len() as usize <= MAX_FILE_SAMPLES * channels,
        "WAV too large"
    );
    mono.try_reserve_exact(reader.duration() as usize)?;
    let mut sum = 0i32;
    let mut count = 0;
    for sample in reader.samples::<i16>() {
        sum += sample? as i32;
        count += 1;
        if count == channels {
            mono.push((sum / channels as i32) as i16);
            sum = 0;
            count = 0;
        }
    }
    ensure!(count == 0, "incomplete WAV frame");
    Ok(mono_resample(&mono, spec.sample_rate)?.into())
}
pub fn mp3(path: &Path) -> Result<Vec<i16>> {
    let stream = MediaSourceStream::new(Box::new(File::open(path)?), Default::default());
    let mut hint = Hint::new();
    hint.with_extension("mp3");
    let probed = symphonia::default::get_probe().format(
        &hint,
        stream,
        &FormatOptions::default(),
        &MetadataOptions::default(),
    )?;
    let mut format = probed.format;
    let track = format.default_track().context("MP3 has no track")?;
    let id = track.id;
    let mut decoder =
        symphonia::default::get_codecs().make(&track.codec_params, &DecoderOptions::default())?;
    let mut output = Vec::new();
    let mut rate = None;
    loop {
        let packet = match format.next_packet() {
            Ok(p) => p,
            Err(DecodeError::IoError(e)) if e.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(e) => return Err(e).context("reading MP3 packet"),
        };
        if packet.track_id() != id {
            continue;
        }
        let decoded = decoder.decode(&packet).context("decoding MP3")?;
        let spec = *decoded.spec();
        if let Some(previous) = rate {
            ensure!(previous == spec.rate, "sample rate changes in MP3");
        }
        rate = Some(spec.rate);
        let channels = spec.channels.count();
        ensure!((1..=2).contains(&channels), "MP3 must be mono or stereo");
        let mut samples = SampleBuffer::<i16>::new(decoded.capacity() as u64, spec);
        samples.copy_interleaved_ref(decoded);
        reserve(
            &mut output,
            samples.samples().len() / channels,
            MAX_FILE_SAMPLES,
        )?;
        for frame in samples.samples().chunks_exact(channels) {
            let sum: i32 = frame.iter().map(|v| *v as i32).sum();
            output.push((sum / channels as i32) as i16);
        }
    }
    let rate = rate.context("empty MP3")?;
    ensure!(!output.is_empty(), "empty decoded MP3");
    if rate == RATE {
        Ok(output)
    } else {
        mono_resample(&output, rate).with_context(|| format!("resampling {}", path.display()))
    }
}
pub fn segment<'a>(samples: &'a [i16], track: &Track) -> Result<&'a [i16]> {
    let start = (track.start_seconds as usize)
        .checked_mul(RATE as usize)
        .context("segment start overflow")?;
    ensure!(
        start < samples.len(),
        "segment starts past EOF in {}",
        track.file
    );
    // The reference uses 500-second caps to mean through EOF for many tracks.
    let end = if let Some(seconds) = track.duration_seconds {
        start
            .checked_add(
                (seconds as usize)
                    .checked_mul(RATE as usize)
                    .context("duration overflow")?,
            )
            .context("segment end overflow")?
            .min(samples.len())
    } else {
        samples.len()
    };
    samples.get(start..end).context("invalid segment")
}
pub fn playlist(root: &Path, tracks: &[Track]) -> Result<Samples> {
    let mut output = Vec::new();
    for track in tracks {
        let decoded = mp3(&root.join("Audio").join(&track.file))
            .with_context(|| format!("preparing {}", track.file))?;
        let clip = segment(&decoded, track)?;
        reserve(&mut output, clip.len(), MAX_SAMPLES)?;
        output.extend_from_slice(clip);
    }
    ensure!(!output.is_empty(), "empty playlist");
    Ok(output.into())
}
pub struct VoiceBank {
    prompts: BTreeMap<String, Samples>,
}
impl VoiceBank {
    pub fn load(root: &Path) -> Result<Self> {
        let mut prompts = BTreeMap::new();
        let mut total = 0usize;
        let mut add = |name: String| -> Result<()> {
            let samples = wav(&root.join("voice").join(&name))?;
            total = total
                .checked_add(samples.len())
                .context("voice size overflow")?;
            ensure!(
                total <= 96 * 1024 * 1024,
                "voice bank exceeds 192 MiB PCM budget"
            );
            prompts.insert(name, samples);
            Ok(())
        };
        for name in ["mode-reading.wav", "mode-noise.wav", "error.wav"] {
            add(name.into())?;
        }
        for hour in 1..=12 {
            for minute in 0..60 {
                add(format!("current-{hour}-{minute:02}.wav"))?;
                if minute % 15 == 0 {
                    add(format!("wake-{hour}-{minute:02}.wav"))?;
                }
            }
        }
        Ok(Self { prompts })
    }
    pub fn get(&self, name: &str) -> Result<Samples> {
        self.prompts
            .get(name)
            .cloned()
            .context("missing preloaded voice prompt")
    }
    pub fn time(&self, kind: &str, hour: u8, minute: u8) -> Result<Samples> {
        self.get(&format!("{kind}-{hour}-{minute:02}.wav"))
    }
}
pub fn validate_all(root: &Path, schedule: &Schedule) -> Result<()> {
    let mut references: BTreeMap<&str, Vec<&Track>> = BTreeMap::new();
    for tracks in schedule.days.values() {
        for track in tracks {
            references.entry(&track.file).or_default().push(track);
        }
    }
    let mut lengths = BTreeMap::new();
    for (i, (file, tracks)) in references.iter().enumerate() {
        let samples = mp3(&root.join("Audio").join(file))?;
        for track in tracks {
            lengths.insert(
                (
                    track.file.clone(),
                    track.start_seconds,
                    track.duration_seconds,
                ),
                segment(&samples, track)?.len(),
            );
        }
        if i % 100 == 0 {
            eprintln!("validated {}/{} reading files", i + 1, references.len());
        }
    }
    let mut largest = 0;
    let mut longest_tracks: Option<&[Track]> = None;
    for tracks in schedule.days.values() {
        let length: usize = tracks
            .iter()
            .map(|track| {
                lengths
                    .get(&(
                        track.file.clone(),
                        track.start_seconds,
                        track.duration_seconds,
                    ))
                    .copied()
                    .unwrap_or(0)
            })
            .sum();
        if length > MAX_SAMPLES {
            bail!("playlist exceeds 192 MiB PCM budget");
        }
        if length > largest {
            largest = length;
            longest_tracks = Some(tracks);
        }
    }
    let prepared = playlist(root, longest_tracks.context("no playlist to validate")?)?;
    ensure!(
        prepared.len() == largest,
        "prepared playlist length differs from validated segments"
    );
    println!("Validated {} unique MP3 files, all segments, and 366 playlists. Longest playlist: {:.1} minutes, {:.1} MiB PCM.",references.len(), largest as f64/RATE as f64/60.0,largest as f64*2.0/1024.0/1024.0);
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn repeated_decode_frames_use_bounded_capacity_growth() -> Result<()> {
        let cap = 1024 * 1024;
        let mut samples = Vec::new();
        let mut growths = 0;
        for _ in 0..1024 {
            let previous = samples.capacity();
            reserve(&mut samples, 1024, cap)?;
            if samples.capacity() != previous {
                growths += 1;
            }
            samples.resize(samples.len() + 1024, 0);
            assert!(samples.capacity() <= cap);
        }
        assert!(growths < 16, "capacity must not grow once per frame");
        assert!(reserve(&mut samples, 1, cap).is_err());
        assert_eq!(samples.len(), cap);
        Ok(())
    }
    #[test]
    fn segment_caps_at_eof_and_rejects_missing_start() {
        let samples = vec![1; RATE as usize * 3];
        let track = Track {
            file: "test.mp3".into(),
            start_seconds: 1,
            duration_seconds: Some(500),
        };
        assert_eq!(
            segment(&samples, &track).map(|v| v.len()).ok(),
            Some(RATE as usize * 2)
        );
        assert!(segment(
            &samples,
            &Track {
                start_seconds: 4,
                ..track
            }
        )
        .is_err());
    }
    #[test]
    fn sample_count_arithmetic_supports_32_bit_pi() {
        // The intermediate product exceeds u32 even for a ten-minute track.
        assert_eq!(
            resampled_len(10 * 60 * 22_050, 22_050).ok(),
            Some(10 * 60 * 44_100)
        );
        assert!(resampled_len(1, 0).is_err());
    }
}
