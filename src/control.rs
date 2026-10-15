use crate::settings::Settings;
use chrono::{DateTime, Duration, LocalResult, TimeZone, Timelike, Utc};
use chrono_tz::America::Chicago;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Command {
    Toggle,
    Play,
    Pause,
    Stop,
    VolumeUp,
    VolumeDown,
    MuteToggle,
    Mode,
    AlarmLeft,
    AlarmRight,
    AlarmUp,
    AlarmDown,
    CurrentTime,
    BluetoothPair,
}
impl Command {
    /// Linux input-event codes. Releases are ignored; only adjustments repeat.
    pub fn from_key(code: u16, value: i32) -> Option<Self> {
        let command = match code {
            352 | 164 => Self::Toggle,
            207 => Self::Play,
            119 => Self::Pause,
            128 => Self::Stop,
            115 => Self::VolumeUp,
            114 => Self::VolumeDown,
            113 => Self::MuteToggle,
            402 | 403 => Self::Mode,
            105 => Self::AlarmLeft,
            106 => Self::AlarmRight,
            103 => Self::AlarmUp,
            108 => Self::AlarmDown,
            358 => Self::CurrentTime,
            401 => Self::BluetoothPair,
            _ => return None,
        };
        if value == 1
            || (value == 2
                && matches!(
                    command,
                    Self::VolumeUp
                        | Self::VolumeDown
                        | Self::AlarmLeft
                        | Self::AlarmRight
                        | Self::AlarmUp
                        | Self::AlarmDown
                ))
        {
            Some(command)
        } else {
            None
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Playback {
    Reading,
    Noise,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Idle,
    Starting,
    Reading,
    Noise,
    Paused(Playback),
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Effect {
    Begin,
    Resume,
    Pause,
    Cancel,
    SpeakTime(u8, u8),
    SpeakCurrentTime(u8, u8),
    SpeakMode(bool),
    SaveSettings,
    Unavailable,
    BluetoothPair,
}

pub struct Engine {
    pub settings: Settings,
    pub state: State,
    pub deadline: Option<DateTime<Utc>>,
    pub volume: u8,
    pub muted: bool,
    start_reading: bool,
}
impl Engine {
    pub fn new(settings: Settings) -> Self {
        Self {
            settings,
            state: State::Idle,
            deadline: None,
            volume: 50,
            muted: false,
            start_reading: false,
        }
    }
    pub fn apply(&mut self, command: Command, now: DateTime<Utc>) -> Vec<Effect> {
        let mut effects = self.tick(now);
        let configuring = matches!(self.state, State::Idle | State::Paused(_));
        match command {
            Command::Toggle => match self.state {
                State::Idle | State::Paused(_) => self.play(now, &mut effects),
                State::Reading | State::Noise => self.pause(&mut effects),
                State::Starting => {}
            },
            Command::Play => self.play(now, &mut effects),
            Command::Pause => self.pause(&mut effects),
            Command::Stop => {
                self.abort_start();
                // Also cancel a voice indication while otherwise idle.
                effects.push(Effect::Cancel);
            }
            Command::VolumeUp => self.volume = self.volume.saturating_add(5).min(100),
            Command::VolumeDown => self.volume = self.volume.saturating_sub(5),
            Command::MuteToggle => self.muted = !self.muted,
            Command::Mode if configuring => {
                self.settings.reading = !self.settings.reading;
                effects.extend([
                    Effect::SaveSettings,
                    Effect::SpeakMode(self.settings.reading),
                ]);
            }
            Command::AlarmLeft | Command::AlarmRight | Command::AlarmUp | Command::AlarmDown
                if configuring =>
            {
                match command {
                    Command::AlarmLeft => self.settings.minute = (self.settings.minute + 45) % 60,
                    Command::AlarmRight => self.settings.minute = (self.settings.minute + 15) % 60,
                    Command::AlarmUp => self.settings.hour = self.settings.hour % 12 + 1,
                    Command::AlarmDown => self.settings.hour = (self.settings.hour + 10) % 12 + 1,
                    _ => {}
                }
                effects.extend([
                    Effect::SaveSettings,
                    Effect::SpeakTime(self.settings.hour, self.settings.minute),
                ]);
            }
            Command::CurrentTime if configuring => {
                let local = now.with_timezone(&Chicago);
                effects.push(Effect::SpeakCurrentTime(
                    display_hour(local.hour()),
                    local.minute() as u8,
                ));
            }
            Command::BluetoothPair if self.state == State::Idle => {
                effects.push(Effect::BluetoothPair);
            }
            _ => {}
        }
        effects
    }
    fn play(&mut self, now: DateTime<Utc>, effects: &mut Vec<Effect>) {
        match self.state {
            State::Idle => match next_deadline(now, &self.settings) {
                Some(deadline) => {
                    self.deadline = Some(deadline);
                    self.start_reading = self.settings.reading;
                    self.state = State::Starting;
                    effects.push(Effect::Begin);
                }
                None => effects.push(Effect::Unavailable),
            },
            State::Paused(kind) => {
                self.state = match kind {
                    Playback::Reading => State::Reading,
                    Playback::Noise => State::Noise,
                };
                effects.push(Effect::Resume);
            }
            _ => {}
        }
    }
    fn pause(&mut self, effects: &mut Vec<Effect>) {
        let kind = match self.state {
            State::Reading => Playback::Reading,
            State::Noise => Playback::Noise,
            _ => return,
        };
        self.state = State::Paused(kind);
        effects.push(Effect::Pause);
    }
    /// Call after preparation and the wake-time announcement complete.
    pub fn start_ready(&mut self) -> bool {
        if self.state != State::Starting {
            return false;
        }
        self.state = if self.start_reading {
            State::Reading
        } else {
            State::Noise
        };
        true
    }
    pub fn reading_finished(&mut self) -> bool {
        if self.state != State::Reading {
            return false;
        }
        self.state = State::Noise;
        true
    }
    pub fn abort_start(&mut self) {
        self.state = State::Idle;
        self.deadline = None;
    }
    pub fn tick(&mut self, now: DateTime<Utc>) -> Vec<Effect> {
        if self.deadline.is_some_and(|deadline| now >= deadline) {
            self.abort_start();
            vec![Effect::Cancel]
        } else {
            Vec::new()
        }
    }
}
fn display_hour(hour: u32) -> u8 {
    let hour = hour % 12;
    if hour == 0 {
        12
    } else {
        hour as u8
    }
}

/// Select the next alarm by the local time shown at bedtime, then convert to UTC.
/// A passed local time stays passed even if fall-back repeats that hour.
pub fn next_deadline(now: DateTime<Utc>, settings: &Settings) -> Option<DateTime<Utc>> {
    if settings.validate().is_err() {
        return None;
    }
    let local_now = now.with_timezone(&Chicago).naive_local();
    let date = local_now.date();
    let mut best = None;
    for offset in 0..=1 {
        let day = date.checked_add_signed(Duration::days(offset))?;
        for hour in [settings.hour as u32 % 12, settings.hour as u32 % 12 + 12] {
            let local = day.and_hms_opt(hour, settings.minute as u32, 0)?;
            if local <= local_now {
                continue;
            }
            let candidates = match Chicago.from_local_datetime(&local) {
                LocalResult::Single(t) => [Some(t), None],
                LocalResult::Ambiguous(a, b) => [Some(a), Some(b)],
                LocalResult::None => [None, None],
            };
            for candidate in candidates.into_iter().flatten() {
                let utc = candidate.with_timezone(&Utc);
                if utc > now && best.is_none_or(|previous| utc < previous) {
                    best = Some(utc);
                }
            }
        }
    }
    best
}

/// Zero-based 366-day cycle from elapsed 24-hour periods, exactly as the
/// reference JavaScript's timestamp subtraction. DST therefore shifts the
/// local boundary rather than changing the elapsed-time schedule.
pub fn reading_day(now: DateTime<Utc>) -> usize {
    match Chicago.with_ymd_and_hms(2020, 4, 5, 3, 0, 0).single() {
        Some(anchor) => {
            let elapsed = now.signed_duration_since(anchor.with_timezone(&Utc));
            let seconds = elapsed.num_seconds();
            // Include a negative fractional second in the mathematical floor.
            let seconds = if elapsed.subsec_nanos() < 0 {
                seconds - 1
            } else {
                seconds
            };
            seconds.div_euclid(86_400).rem_euclid(366) as usize
        }
        None => 0, // The constant Chicago anchor is valid and unambiguous.
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    fn local(year: i32, month: u32, day: u32, hour: u32, minute: u32) -> DateTime<Utc> {
        Chicago
            .with_ymd_and_hms(year, month, day, hour, minute, 0)
            .single()
            .unwrap()
            .with_timezone(&Utc)
    }
    #[test]
    fn next_am_or_pm() {
        let settings = Settings::default();
        assert_eq!(
            next_deadline(local(2026, 10, 5, 21, 0), &settings),
            Some(local(2026, 10, 6, 6, 30))
        );
        assert_eq!(
            next_deadline(local(2026, 10, 5, 17, 0), &settings),
            Some(local(2026, 10, 5, 18, 30))
        );
    }
    #[test]
    fn pause_configure_resume_preserves_session() {
        let now = local(2026, 10, 5, 21, 0);
        let mut engine = Engine::new(Settings::default());
        assert_eq!(engine.apply(Command::Play, now), vec![Effect::Begin]);
        assert!(engine.start_ready());
        let deadline = engine.deadline;
        assert_eq!(engine.apply(Command::Pause, now), vec![Effect::Pause]);
        assert_eq!(
            engine.apply(Command::AlarmUp, now),
            vec![Effect::SaveSettings, Effect::SpeakTime(7, 30)]
        );
        assert_eq!(engine.apply(Command::Play, now), vec![Effect::Resume]);
        assert_eq!(engine.deadline, deadline);
        assert!(engine.apply(Command::CurrentTime, now).is_empty());
        engine.apply(Command::Pause, now);
        assert_eq!(
            engine.apply(Command::CurrentTime, now),
            vec![Effect::SpeakCurrentTime(9, 0)]
        );
        assert_eq!(
            engine.tick(deadline.unwrap() + Duration::seconds(1)),
            vec![Effect::Cancel]
        );
        assert_eq!(engine.state, State::Idle);
    }
    #[test]
    fn controls_suppressed_during_reading_and_noise() {
        let now = local(2026, 10, 5, 21, 0);
        let mut engine = Engine::new(Settings::default());
        engine.apply(Command::Play, now);
        engine.start_ready();
        for command in [Command::Mode, Command::AlarmLeft, Command::CurrentTime] {
            assert!(engine.apply(command, now).is_empty());
        }
        assert!(engine.reading_finished());
        assert!(engine.apply(Command::Mode, now).is_empty());
        assert_eq!(engine.apply(Command::Stop, now), vec![Effect::Cancel]);
        assert!(engine.deadline.is_none());
    }
    #[test]
    fn input_repeat_policy() {
        assert_eq!(Command::from_key(401, 1), Some(Command::BluetoothPair));
        assert_eq!(Command::from_key(401, 0), None);
        assert_eq!(Command::from_key(401, 2), None);
        assert_eq!(Command::from_key(352, 1), Some(Command::Toggle));
        assert_eq!(Command::from_key(352, 2), None);
        assert_eq!(Command::from_key(115, 2), Some(Command::VolumeUp));
        assert_eq!(Command::from_key(105, 2), Some(Command::AlarmLeft));
        assert_eq!(Command::from_key(358, 1), Some(Command::CurrentTime));
        assert_eq!(Command::from_key(358, 0), None);
        assert_eq!(Command::from_key(358, 2), None);
    }
    #[test]
    fn blue_button_pairs_only_while_idle_including_not_while_paused() {
        let now = local(2026, 10, 5, 21, 0);
        let mut engine = Engine::new(Settings::default());
        assert_eq!(
            engine.apply(Command::BluetoothPair, now),
            vec![Effect::BluetoothPair]
        );
        engine.apply(Command::Play, now);
        assert!(engine.apply(Command::BluetoothPair, now).is_empty());
        engine.start_ready();
        assert!(engine.apply(Command::BluetoothPair, now).is_empty());
        engine.apply(Command::Pause, now);
        assert!(engine.apply(Command::BluetoothPair, now).is_empty());
        engine.apply(Command::Play, now);
        engine.reading_finished();
        assert!(engine.apply(Command::BluetoothPair, now).is_empty());
        engine.apply(Command::Pause, now);
        assert!(engine.apply(Command::BluetoothPair, now).is_empty());
        engine.apply(Command::Stop, now);
        assert_eq!(
            engine.apply(Command::BluetoothPair, now),
            vec![Effect::BluetoothPair]
        );
    }
    #[test]
    fn dst_selects_next_existing_occurrence_without_a_duration_cap() {
        let settings = Settings {
            hour: 2,
            minute: 30,
            reading: false,
        };
        assert_eq!(
            next_deadline(local(2026, 3, 7, 22, 0), &settings),
            Some(local(2026, 3, 8, 14, 30))
        );
        let settings = Settings {
            hour: 6,
            minute: 30,
            reading: false,
        };
        assert_eq!(
            next_deadline(local(2026, 10, 31, 18, 31), &settings),
            Some(local(2026, 11, 1, 6, 30))
        );
    }
    #[test]
    fn reading_boundary_and_cycle() {
        assert_eq!(reading_day(local(2020, 4, 5, 3, 0)), 0);
        assert_eq!(reading_day(local(2020, 4, 6, 2, 59)), 0);
        assert_eq!(reading_day(local(2020, 4, 6, 3, 0)), 1);
        assert_eq!(reading_day(local(2021, 4, 6, 3, 0)), 0);
        // The anchor is CDT. During winter, elapsed 24-hour boundaries fall
        // at 02:00 CST, preserving the original schedule timestamp behavior.
        let before = local(2020, 11, 3, 1, 59);
        let after = local(2020, 11, 3, 2, 0);
        assert_eq!((reading_day(before) + 1) % 366, reading_day(after));
        assert_eq!(
            reading_day(local(2020, 4, 5, 3, 0) - Duration::nanoseconds(1)),
            365
        );
    }

    #[test]
    fn adjustments_wrap_independently_and_volume_stays_bounded() {
        let now = local(2026, 10, 5, 17, 0);
        let mut engine = Engine::new(Settings {
            hour: 12,
            minute: 45,
            reading: false,
        });
        engine.apply(Command::AlarmRight, now);
        assert_eq!((engine.settings.hour, engine.settings.minute), (12, 0));
        engine.apply(Command::AlarmUp, now);
        assert_eq!(engine.settings.hour, 1);
        engine.apply(Command::AlarmDown, now);
        engine.apply(Command::AlarmLeft, now);
        assert_eq!((engine.settings.hour, engine.settings.minute), (12, 45));
        for _ in 0..100 {
            engine.apply(Command::VolumeUp, now);
        }
        assert_eq!(engine.volume, 100);
        for _ in 0..100 {
            engine.apply(Command::VolumeDown, now);
        }
        assert_eq!(engine.volume, 0);
        engine.apply(Command::MuteToggle, now);
        assert!(engine.muted);
        engine.apply(Command::MuteToggle, now);
        assert!(!engine.muted);
    }

    #[test]
    fn unavailable_deadline_does_not_arm_and_failed_start_is_idle() {
        let now = local(2026, 3, 7, 22, 0);
        let mut engine = Engine::new(Settings {
            hour: 0,
            minute: 30,
            reading: false,
        });
        assert_eq!(engine.apply(Command::Play, now), vec![Effect::Unavailable]);
        assert_eq!(engine.state, State::Idle);
        assert!(engine.deadline.is_none());
        let now = local(2026, 10, 5, 17, 0);
        engine.settings.hour = 2;
        assert_eq!(engine.apply(Command::Play, now), vec![Effect::Begin]);
        engine.abort_start();
        assert_eq!(engine.state, State::Idle);
        assert!(engine.deadline.is_none());
        assert!(!engine.start_ready());
    }
}
