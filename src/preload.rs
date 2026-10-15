//! One resident daily playlist. Refresh only at an approved silent opportunity.
use crate::{control, media::Samples};
use anyhow::Result;
use chrono::{DateTime, Timelike, Utc};
use chrono_tz::America::Chicago;

pub struct DailyPlaylist {
    day: usize,
    samples: Samples,
}

impl DailyPlaylist {
    pub fn new(now: DateTime<Utc>, load: impl FnOnce(usize) -> Result<Samples>) -> Result<Self> {
        let day = control::reading_day(now);
        Ok(Self {
            day,
            samples: load(day)?,
        })
    }

    pub fn due(&self, now: DateTime<Utc>) -> bool {
        now.with_timezone(&Chicago).hour() >= 3 && control::reading_day(now) != self.day
    }

    /// The caller must hold the control loop idle after the audio thread confirms silence.
    pub fn refresh(
        &mut self,
        now: DateTime<Utc>,
        silent: bool,
        load: impl FnOnce(usize) -> Result<Samples>,
    ) -> Result<()> {
        if silent && self.due(now) {
            let day = control::reading_day(now);
            let samples = load(day)?;
            self.samples = samples;
            self.day = day;
        }
        Ok(())
    }

    pub fn samples(&self) -> Samples {
        self.samples.clone()
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used)]
mod tests {
    use super::*;
    use chrono::TimeZone;
    use std::sync::Arc;

    fn local(day: u32, hour: u32) -> DateTime<Utc> {
        Chicago
            .with_ymd_and_hms(2026, 12, day, hour, 0, 0)
            .single()
            .unwrap()
            .with_timezone(&Utc)
    }

    #[test]
    fn startup_loads_once_and_repeated_play_reuses_same_buffer() {
        let samples: Samples = vec![1, 2].into();
        let cache = DailyPlaylist::new(local(1, 21), |_| Ok(samples.clone())).unwrap();
        assert!(Arc::ptr_eq(&cache.samples(), &samples));
        assert!(Arc::ptr_eq(&cache.samples(), &cache.samples()));
    }

    #[test]
    fn rollover_waits_until_after_three_and_silent_then_loads_once() {
        let mut cache = DailyPlaylist::new(local(1, 21), |_| Ok(vec![1].into())).unwrap();
        // In winter the elapsed-time schedule advances at 2 a.m.; loading still waits for 3.
        assert_ne!(
            control::reading_day(local(1, 21)),
            control::reading_day(local(2, 2))
        );
        cache
            .refresh(local(2, 2), true, |_| panic!("too early"))
            .unwrap();
        cache
            .refresh(local(2, 3), false, |_| panic!("audio is active"))
            .unwrap();
        cache
            .refresh(local(2, 8), true, |_| Ok(vec![2].into()))
            .unwrap();
        assert_eq!(&*cache.samples(), &[2]);
        cache
            .refresh(local(2, 9), true, |_| panic!("already loaded"))
            .unwrap();
    }

    #[test]
    fn failed_refresh_keeps_previous_playlist_and_can_retry() {
        let mut cache = DailyPlaylist::new(local(1, 21), |_| Ok(vec![1].into())).unwrap();
        assert!(cache
            .refresh(local(2, 3), true, |_| anyhow::bail!("bad file"))
            .is_err());
        assert_eq!(&*cache.samples(), &[1]);
        assert!(cache.due(local(2, 4)));
        cache
            .refresh(local(2, 4), true, |_| Ok(vec![2].into()))
            .unwrap();
        assert!(!cache.due(local(2, 4)));
    }
}
