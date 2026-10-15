use crate::media::{Samples, RATE};
use alsa::{
    pcm::{Access, Format, HwParams, PCM},
    Direction, ValueOr,
};
use anyhow::{ensure, Context, Result};
use std::{
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, SyncSender, TryRecvError},
        Arc,
    },
    thread,
    time::{Duration, Instant},
};

const FRAMES: usize = 256;
pub enum Message {
    CheckSilence(SyncSender<bool>),
    Prepare {
        announcement: Samples,
        id: u64,
        deadline: chrono::DateTime<chrono::Utc>,
    },
    Ready {
        reading: Option<Samples>,
        id: u64,
    },
    Begin {
        reading: Option<Samples>,
        announcement: Samples,
        id: u64,
        deadline: chrono::DateTime<chrono::Utc>,
    },
    Prompt(Samples),
    Pause,
    Resume,
    Cancel,
    Gain(u8, bool),
    BluetoothEnabled(bool),
}
#[derive(Debug)]
pub enum Event {
    Started(u64),
    ReadingFinished(u64),
    DeadlineReached(u64),
    Fault(String),
    Retired(Samples),
}
#[derive(Clone, Copy, PartialEq, Debug)]
enum Phase {
    Idle,
    Preparing,
    Announcing,
    Reading,
    Noise,
    PausedReading,
    PausedNoise,
}
struct Mixer {
    noise: Samples,
    reading: Option<Samples>,
    prompt: Option<Samples>,
    prompt_position: usize,
    position: usize,
    phase: Phase,
    retired: Option<Samples>,
    volume: u8,
    muted: bool,
    id: u64,
    deadline: Option<chrono::DateTime<chrono::Utc>>,
    bluetooth_enabled: bool,
}
impl Mixer {
    fn can_preload(&self) -> bool {
        self.phase == Phase::Idle && self.prompt.is_none() && !self.bluetooth_enabled
    }
    fn new(noise: Samples) -> Self {
        Self {
            noise,
            reading: None,
            prompt: None,
            prompt_position: 0,
            position: 0,
            phase: Phase::Idle,
            retired: None,
            volume: 50,
            muted: false,
            id: 0,
            deadline: None,
            bluetooth_enabled: false,
        }
    }
    fn command(&mut self, message: Message) {
        match message {
            Message::Prepare {
                announcement,
                id,
                deadline,
            } => {
                self.command(Message::Begin {
                    reading: None,
                    announcement,
                    id,
                    deadline,
                });
                self.phase = Phase::Preparing;
            }
            Message::Ready { reading, id } => {
                if self.phase == Phase::Preparing && id == self.id {
                    self.reading = reading;
                    self.phase = Phase::Announcing;
                } else {
                    self.retired = reading;
                }
            }
            Message::Begin {
                reading,
                announcement,
                id,
                deadline,
            } => {
                self.bluetooth_enabled = false;
                self.id = id;
                self.deadline = Some(deadline);
                self.retired = self.reading.take();
                self.reading = reading;
                self.prompt = Some(announcement);
                self.prompt_position = 0;
                self.position = 0;
                self.phase = Phase::Announcing;
            }
            Message::Prompt(prompt)
                if matches!(
                    self.phase,
                    Phase::Idle | Phase::PausedReading | Phase::PausedNoise
                ) =>
            {
                self.prompt = Some(prompt);
                self.prompt_position = 0;
            }
            Message::Pause => {
                self.phase = match self.phase {
                    Phase::Reading => Phase::PausedReading,
                    Phase::Noise => Phase::PausedNoise,
                    other => other,
                };
            }
            Message::Resume => {
                self.prompt = None;
                self.prompt_position = 0;
                self.phase = match self.phase {
                    Phase::PausedReading => Phase::Reading,
                    Phase::PausedNoise => Phase::Noise,
                    other => other,
                };
            }
            Message::Cancel => {
                self.bluetooth_enabled = false;
                self.phase = Phase::Idle;
                self.retired = self.reading.take();
                self.prompt = None;
                self.position = 0;
                self.deadline = None;
            }
            Message::Gain(volume, muted) => {
                self.volume = volume.min(100);
                self.muted = muted;
            }
            Message::BluetoothEnabled(enabled) => self.bluetooth_enabled = enabled,
            _ => {}
        }
    }
    fn expire(&mut self, now: chrono::DateTime<chrono::Utc>) -> Option<u64> {
        if self.deadline.is_some_and(|deadline| now >= deadline) {
            let id = self.id;
            self.command(Message::Cancel);
            Some(id)
        } else {
            None
        }
    }
    fn sample(&mut self, started: &mut bool, finished: &mut bool) -> i16 {
        if self.phase == Phase::Announcing && self.prompt.is_none() {
            self.phase = if self.reading.is_some() {
                Phase::Reading
            } else {
                Phase::Noise
            };
            *started = true;
        }
        let sample = if let Some(prompt) = &self.prompt {
            if let Some(sample) = prompt.get(self.prompt_position).copied() {
                self.prompt_position += 1;
                sample
            } else {
                self.prompt = None;
                if self.phase == Phase::Announcing {
                    self.phase = if self.reading.is_some() {
                        Phase::Reading
                    } else {
                        Phase::Noise
                    };
                    *started = true;
                }
                return self.sample(started, finished);
            }
        } else {
            match self.phase {
                Phase::Reading => {
                    let sample = self
                        .reading
                        .as_ref()
                        .and_then(|samples| samples.get(self.position))
                        .copied();
                    if let Some(sample) = sample {
                        self.position += 1;
                        sample
                    } else {
                        self.phase = Phase::Noise;
                        self.position = 0;
                        *finished = true;
                        return self.sample(started, finished);
                    }
                }
                Phase::Noise => {
                    let sample = self.noise.get(self.position).copied().unwrap_or(0);
                    self.position = (self.position + 1) % self.noise.len();
                    sample
                }
                _ => 0,
            }
        };
        if self.muted {
            0
        } else {
            ((sample as i32 * self.volume as i32) / 100) as i16
        }
    }
    fn fill(&mut self, buffer: &mut [i16]) -> (bool, bool) {
        self.fill_bluetooth(buffer, &[])
    }
    fn fill_bluetooth(&mut self, buffer: &mut [i16], bluetooth: &[i16]) -> (bool, bool) {
        let (mut started, mut finished) = (false, false);
        for (index, frame) in buffer.chunks_exact_mut(2).enumerate() {
            if self.phase == Phase::Idle && self.bluetooth_enabled && self.prompt.is_none() {
                for (channel, sample) in frame.iter_mut().enumerate() {
                    let source = bluetooth.get(index * 2 + channel).copied().unwrap_or(0);
                    *sample = if self.muted {
                        0
                    } else {
                        ((source as i32 * self.volume as i32) / 100) as i16
                    };
                }
            } else {
                let sample = self.sample(&mut started, &mut finished);
                frame.fill(sample);
            }
        }
        (started, finished)
    }
}
pub struct Output {
    pub commands: SyncSender<Message>,
    pub events: Receiver<Event>,
    pub stop: Arc<AtomicBool>,
    thread: Option<thread::JoinHandle<()>>,
}
impl Output {
    pub fn open(device: &str, noise: Samples) -> Result<Self> {
        ensure!(!noise.is_empty(), "empty noise loop");
        // Optional device initialization must not prevent otherwise working playback.
        if let Err(error) = crate::hardware_volume::initialize(device) {
            eprintln!("shush: hardware volume unchanged: {error:#}");
        }
        let pcm = PCM::new(device, Direction::Playback, true).context("opening ALSA output")?;
        {
            let hw = HwParams::any(&pcm)?;
            hw.set_access(Access::RWInterleaved)?;
            hw.set_format(Format::s16())?;
            hw.set_channels(2)?;
            hw.set_rate(RATE, ValueOr::Nearest)?;
            hw.set_buffer_time_near(200_000, ValueOr::Nearest)?;
            hw.set_period_time_near(10_000, ValueOr::Nearest)?;
            pcm.hw_params(&hw)?;
            let actual = pcm.hw_params_current()?;
            ensure!(
                actual.get_rate()? == RATE,
                "ALSA output must accept {RATE} Hz; use plughw for USB rate conversion"
            );
            ensure!(
                i128::from(actual.get_buffer_size()?) * 1_000_000 / RATE as i128 <= 250_000,
                "ALSA buffer exceeds 250 ms timing bound"
            );
            let sw = pcm.sw_params_current()?;
            sw.set_start_threshold(actual.get_buffer_size()? / 2)?;
            pcm.sw_params(&sw)?;
        }
        let (commands, rx) = mpsc::sync_channel(32);
        let (tx, events) = mpsc::sync_channel(8);
        let stop = Arc::new(AtomicBool::new(false));
        let worker_stop = stop.clone();
        let thread = thread::Builder::new()
            .name("shush-audio".into())
            .spawn(move || {
                let result = stream(pcm, Mixer::new(noise), rx, &tx, &worker_stop);
                if let Err(error) = result {
                    let _ = tx.try_send(Event::Fault(format!("{error:#}")));
                }
                worker_stop.store(true, Ordering::Release);
            })?;
        Ok(Self {
            commands,
            events,
            stop,
            thread: Some(thread),
        })
    }
    /// Bounded backpressure is confined to the control thread. The independent
    /// audio thread owns the wake deadline and never waits for this producer.
    pub fn send(&self, message: Message) -> Result<()> {
        self.commands
            .send(message)
            .context("audio command queue unavailable")
    }

    /// Called only by the control thread, which sends no sound commands while loading.
    pub fn is_silent(&self) -> Result<bool> {
        let (tx, rx) = mpsc::sync_channel(1);
        self.send(Message::CheckSilence(tx))?;
        rx.recv_timeout(Duration::from_secs(1))
            .context("audio silence check timed out")
    }
}
impl Drop for Output {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
fn stream(
    pcm: PCM,
    mut mixer: Mixer,
    rx: Receiver<Message>,
    tx: &SyncSender<Event>,
    stop: &AtomicBool,
) -> Result<()> {
    let io = pcm.io_i16()?;
    let mut buffer = [0i16; FRAMES * 2];
    let mut offset = FRAMES;
    let mut last_progress = Instant::now();
    let (mut started, mut finished) = (false, false);
    let mut pending_id = 0;
    let mut bluetooth = crate::bluetooth::Input::open();
    let mut bluetooth_buffer = [0i16; FRAMES * 2];
    let mut silent_since = Instant::now();
    while !stop.load(Ordering::Acquire) {
        // Fixed maximum command work per iteration prevents input floods starving output.
        for _ in 0..32 {
            match rx.try_recv() {
                Ok(message) => {
                    if let Message::CheckSilence(reply) = message {
                        let _ = reply.try_send(
                            mixer.can_preload()
                                && silent_since.elapsed() >= Duration::from_millis(300),
                        );
                        continue;
                    }
                    if mixer.phase == Phase::Idle
                        && matches!(
                            message,
                            Message::Prepare { .. }
                                | Message::Begin { .. }
                                | Message::Cancel
                                | Message::Prompt(_)
                                | Message::BluetoothEnabled(false)
                        )
                    {
                        buffer.fill(0);
                        offset = FRAMES;
                    }
                    mixer.command(message);
                    if !mixer.can_preload() {
                        silent_since = Instant::now();
                    }
                    if let Some(samples) = mixer.retired.take() {
                        tx.try_send(Event::Retired(samples))
                            .context("audio retirement queue full")?;
                    }
                }
                Err(TryRecvError::Empty) => break,
                Err(TryRecvError::Disconnected) => return Ok(()),
            }
        }
        if let Some(id) = mixer.expire(chrono::Utc::now()) {
            // Clear samples generated but not yet submitted. Hardware queue is <=200 ms.
            buffer.fill(0);
            started = false;
            finished = false;
            if let Some(samples) = mixer.retired.take() {
                tx.try_send(Event::Retired(samples))
                    .context("audio retirement queue full")?;
            }
            tx.try_send(Event::DeadlineReached(id))
                .context("audio event queue full")?;
        }
        if offset == FRAMES {
            if let Some(input) = &mut bluetooth {
                input.fill(
                    &mut bluetooth_buffer,
                    mixer.phase == Phase::Idle && mixer.bluetooth_enabled && mixer.prompt.is_none(),
                );
                (started, finished) = mixer.fill_bluetooth(&mut buffer, &bluetooth_buffer);
            } else {
                (started, finished) = mixer.fill(&mut buffer);
            }
            pending_id = mixer.id;
            offset = 0;
        }
        if !mixer.can_preload() {
            silent_since = Instant::now();
        }
        let available = pcm.avail_update()?;
        if available > 0 {
            let frames = (available as usize).min(FRAMES - offset);
            match io.writei(&buffer[offset * 2..(offset + frames) * 2]) {
                Ok(written) if written > 0 => {
                    offset += written;
                    last_progress = Instant::now();
                    if offset == FRAMES {
                        if started {
                            tx.try_send(Event::Started(pending_id))
                                .context("audio event queue full")?;
                        }
                        if finished {
                            tx.try_send(Event::ReadingFinished(pending_id))
                                .context("audio event queue full")?;
                        }
                    }
                }
                Ok(_) => {}
                Err(e) if e.errno() == libc::EAGAIN || e.errno() == libc::EINTR => {}
                Err(e) => {
                    return Err(e).context("ALSA write failed; playback session did not pass")
                }
            }
        } else {
            thread::sleep(Duration::from_millis(2));
        }
        ensure!(
            last_progress.elapsed() < Duration::from_secs(2),
            "ALSA output stopped making progress"
        );
    }
    pcm.drop()?;
    Ok(())
}
#[cfg(test)]
#[path = "audio_expire_tests.rs"]
mod expire_tests;

#[cfg(test)]
mod tests {
    #[test]
    fn preloading_requires_idle_without_prompt_or_bluetooth() {
        use super::*;
        let mut mixer = Mixer::new(vec![1].into());
        assert!(mixer.can_preload());
        for phase in [
            Phase::Preparing,
            Phase::Announcing,
            Phase::Reading,
            Phase::Noise,
            Phase::PausedReading,
            Phase::PausedNoise,
        ] {
            mixer.phase = phase;
            assert!(!mixer.can_preload());
        }
        mixer.phase = Phase::Idle;
        mixer.command(Message::Prompt(vec![1].into()));
        assert!(!mixer.can_preload());
        mixer.command(Message::Cancel);
        mixer.command(Message::BluetoothEnabled(true));
        assert!(!mixer.can_preload());
        mixer.command(Message::Cancel);
        assert!(mixer.can_preload());
    }

    #[test]
    fn bluetooth_is_stereo_only_when_enabled_and_idle_and_never_overlays_an_alarm() {
        use super::*;
        for phase in [
            Phase::Preparing,
            Phase::Announcing,
            Phase::Reading,
            Phase::Noise,
            Phase::PausedReading,
            Phase::PausedNoise,
        ] {
            let mut mixer = Mixer::new(Arc::from([0]));
            mixer.phase = phase;
            mixer.command(Message::BluetoothEnabled(true));
            let mut output = [0; 4];
            mixer.fill_bluetooth(&mut output, &[1000, -2000, 3000, -4000]);
            assert_eq!(output, [0; 4], "Bluetooth leaked into {phase:?}");
        }
        let mut mixer = Mixer::new(Arc::from([0]));
        let mut output = [0; 4];
        mixer.fill_bluetooth(&mut output, &[1000, -2000, 3000, -4000]);
        assert_eq!(output, [0; 4]);
        mixer.command(Message::BluetoothEnabled(true));
        mixer.fill_bluetooth(&mut output, &[1000, -2000, 3000, -4000]);
        assert_eq!(output, [500, -1000, 1500, -2000]);
        mixer.command(Message::Gain(50, true));
        mixer.fill_bluetooth(&mut output, &[1000, -2000, 3000, -4000]);
        assert_eq!(output, [0; 4]);
        mixer.command(Message::Gain(50, false));
        mixer.command(Message::Cancel);
        mixer.fill_bluetooth(&mut output, &[1000, -2000, 3000, -4000]);
        assert_eq!(output, [0; 4]);
    }
    use super::*;
    #[test]
    fn announcement_starts_before_loading_and_finishes_before_reading() {
        for ready_early in [false, true] {
            let mut mixer = Mixer::new(Arc::from([30]));
            mixer.volume = 100;
            mixer.command(Message::Prepare {
                announcement: Arc::from([1, 2]),
                id: 7,
                deadline: chrono::Utc::now() + chrono::Duration::hours(1),
            });
            let mut frame = [0; 2];
            assert_eq!(mixer.fill(&mut frame), (false, false));
            assert_eq!(frame, [1, 1]);
            if ready_early {
                mixer.command(Message::Ready {
                    reading: Some(Arc::from([10, 20])),
                    id: 7,
                });
            }
            assert_eq!(mixer.fill(&mut frame), (false, false));
            assert_eq!(frame, [2, 2]);
            if !ready_early {
                assert_eq!(mixer.fill(&mut frame), (false, false));
                assert_eq!(frame, [0, 0]);
                mixer.command(Message::Ready {
                    reading: Some(Arc::from([10, 20])),
                    id: 7,
                });
            }
            assert_eq!(mixer.fill(&mut frame), (true, false));
            assert_eq!(frame, [10, 10]);
            assert_eq!(mixer.fill(&mut frame), (false, false));
            assert_eq!(frame, [20, 20]);
        }
    }

    #[test]
    fn noise_waits_for_immediate_announcement_without_repeating_it() {
        let mut mixer = Mixer::new(Arc::from([30]));
        mixer.volume = 100;
        mixer.command(Message::Prepare {
            announcement: Arc::from([1, 2]),
            id: 7,
            deadline: chrono::Utc::now() + chrono::Duration::hours(1),
        });
        mixer.command(Message::Ready {
            reading: None,
            id: 7,
        });
        let mut buffer = [0; 6];
        assert_eq!(mixer.fill(&mut buffer), (true, false));
        assert_eq!(buffer, [1, 1, 2, 2, 30, 30]);
    }

    #[test]
    fn cancelling_or_expiring_loading_cannot_be_undone_by_ready() {
        for expire in [false, true] {
            let mut mixer = Mixer::new(Arc::from([30]));
            mixer.volume = 100;
            let now = chrono::Utc::now();
            mixer.command(Message::Prepare {
                announcement: Arc::from([1, 2]),
                id: 7,
                deadline: now + chrono::Duration::seconds(1),
            });
            if expire {
                assert_eq!(mixer.expire(now + chrono::Duration::seconds(2)), Some(7));
            } else {
                mixer.command(Message::Cancel);
            }
            mixer.command(Message::Ready {
                reading: Some(Arc::from([10, 20])),
                id: 7,
            });
            let mut buffer = [0; 6];
            assert_eq!(mixer.fill(&mut buffer), (false, false));
            assert_eq!(buffer, [0; 6]);
            assert_eq!(mixer.phase, Phase::Idle);
        }
    }

    #[test]
    fn playlist_then_noise_without_gaps_and_resume_without_announcement() {
        let mut mixer = Mixer::new(Arc::from([30, 40]));
        mixer.volume = 100;
        mixer.command(Message::Begin {
            reading: Some(Arc::from([10, 20])),
            announcement: Arc::from([1, 2]),
            id: 1,
            deadline: chrono::Utc::now() + chrono::Duration::hours(1),
        });
        let mut buffer = [0; 12];
        assert_eq!(mixer.fill(&mut buffer), (true, true));
        assert_eq!(buffer, [1, 1, 2, 2, 10, 10, 20, 20, 30, 30, 40, 40]);
        mixer.command(Message::Pause);
        let pos = mixer.position;
        mixer.fill(&mut buffer);
        assert_eq!(buffer, [0; 12]);
        assert_eq!(mixer.position, pos);
        mixer.command(Message::Resume);
        assert_eq!(mixer.fill(&mut buffer), (false, false));
        assert_eq!(buffer, [30, 30, 40, 40, 30, 30, 40, 40, 30, 30, 40, 40]);
    }
    #[test]
    fn active_playback_cannot_receive_voice_overlays() {
        let mut mixer = Mixer::new(Arc::from([30]));
        mixer.volume = 100;
        mixer.command(Message::Begin {
            reading: None,
            announcement: Arc::from([1]),
            id: 1,
            deadline: chrono::Utc::now() + chrono::Duration::hours(1),
        });
        let mut buffer = [0; 4];
        mixer.fill(&mut buffer);
        mixer.command(Message::Prompt(Arc::from([999])));
        mixer.fill(&mut buffer);
        assert_eq!(buffer, [30; 4]);
        mixer.command(Message::Gain(100, true));
        mixer.fill(&mut buffer);
        assert_eq!(buffer, [0; 4]);
    }
    #[test]
    fn paused_reading_resumes_at_the_same_sample() {
        let mut mixer = Mixer::new(Arc::from([30]));
        mixer.volume = 100;
        mixer.command(Message::Begin {
            reading: Some(Arc::from([10, 20, 30, 40])),
            announcement: Arc::from([1]),
            id: 2,
            deadline: chrono::Utc::now() + chrono::Duration::hours(1),
        });
        let mut buffer = [0; 4];
        mixer.fill(&mut buffer);
        assert_eq!(buffer, [1, 1, 10, 10]);
        mixer.command(Message::Pause);
        mixer.fill(&mut buffer);
        assert_eq!(buffer, [0; 4]);
        mixer.command(Message::Resume);
        mixer.fill(&mut buffer);
        assert_eq!(buffer, [20, 20, 30, 30]);
    }
    #[test]
    fn deadline_cancels_paused_audio_without_a_control_command() {
        let mut mixer = Mixer::new(Arc::from([30]));
        let now = chrono::Utc::now();
        mixer.command(Message::Begin {
            reading: None,
            announcement: Arc::from([1]),
            id: 42,
            deadline: now + chrono::Duration::seconds(1),
        });
        let mut buffer = [0; 4];
        mixer.fill(&mut buffer);
        mixer.command(Message::Pause);
        assert_eq!(mixer.expire(now + chrono::Duration::seconds(2)), Some(42));
        assert_eq!(mixer.phase, Phase::Idle);
        mixer.command(Message::Resume);
        mixer.fill(&mut buffer);
        assert_eq!(buffer, [0; 4]);
    }
    #[test]
    fn alsa_silence_check_waits_for_queue_clear_and_rejects_active_sessions() -> Result<()> {
        let output = Output::open("null", Arc::from([10, 20]))?;
        thread::sleep(Duration::from_millis(350));
        ensure!(output.is_silent()?, "initial idle output should be silent");
        output.send(Message::Begin {
            reading: None,
            announcement: Arc::from([1]),
            id: 70,
            deadline: chrono::Utc::now() + chrono::Duration::hours(1),
        })?;
        ensure!(!output.is_silent()?, "active noise must prevent loading");
        output.send(Message::Pause)?;
        ensure!(!output.is_silent()?, "paused sessions must prevent loading");
        output.send(Message::Cancel)?;
        ensure!(!output.is_silent()?, "queued speaker audio has not cleared");
        thread::sleep(Duration::from_millis(350));
        ensure!(output.is_silent()?, "stopped output should become silent");
        output.send(Message::BluetoothEnabled(true))?;
        ensure!(
            !output.is_silent()?,
            "Bluetooth session must prevent loading"
        );
        Ok(())
    }

    #[test]
    fn alsa_null_output_enforces_deadline_independently() -> Result<()> {
        let output = Output::open("null", Arc::from([10, 20]))?;
        output.send(Message::Begin {
            reading: None,
            announcement: Arc::from([1, 2]),
            id: 9,
            deadline: chrono::Utc::now() + chrono::Duration::milliseconds(100),
        })?;
        let limit = Instant::now() + Duration::from_secs(2);
        let mut started = false;
        while Instant::now() < limit {
            match output.events.recv_timeout(Duration::from_millis(500))? {
                Event::Started(9) => started = true,
                Event::DeadlineReached(9) => {
                    ensure!(started, "start event missing");
                    return Ok(());
                }
                Event::Fault(error) => anyhow::bail!("{error}"),
                _ => {}
            }
        }
        anyhow::bail!("ALSA deadline event missing")
    }
}
