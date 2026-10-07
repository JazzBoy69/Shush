use super::*;
use crate::control::{Command, Effect, Engine, State};
use crate::settings::Settings;
use chrono::{DateTime, Duration as WallDuration, TimeZone, Utc};
use chrono_tz::America::Chicago;

fn local_time(day: u32, hour: u32, minute: u32) -> Result<DateTime<Utc>> {
    Ok(Chicago
        .with_ymd_and_hms(2026, 10, day, hour, minute, 0)
        .single()
        .context("invalid local test time")?
        .with_timezone(&Utc))
}

fn offset_time(time: &str) -> Result<DateTime<Utc>> {
    Ok(DateTime::parse_from_rfc3339(time)?.with_timezone(&Utc))
}

#[test]
fn fall_back_session_runs_thirteen_hours_to_the_selected_local_alarm() -> Result<()> {
    let start = offset_time("2026-10-31T18:30:00-05:00")?;
    let expected = offset_time("2026-11-01T06:30:00-06:00")?;
    let (mut engine, mut mixer) = start_session(start, Settings::default())?;
    assert_eq!(engine.deadline, Some(expected));
    assert_eq!(expected - start, WallDuration::hours(13));
    assert_not_expired(&mut engine, &mut mixer, start + WallDuration::hours(12));
    assert_not_expired(
        &mut engine,
        &mut mixer,
        expected - WallDuration::nanoseconds(1),
    );
    assert_expires(&mut engine, &mut mixer, expected);
    Ok(())
}

#[test]
fn spring_forward_stops_at_the_selected_local_alarm() -> Result<()> {
    let start = offset_time("2026-03-07T21:00:00-06:00")?;
    let expected = offset_time("2026-03-08T06:30:00-05:00")?;
    let (mut engine, mut mixer) = start_session(start, Settings::default())?;
    assert_eq!(engine.deadline, Some(expected));
    assert_eq!(
        expected - start,
        WallDuration::hours(8) + WallDuration::minutes(30)
    );
    assert_not_expired(&mut engine, &mut mixer, expected - WallDuration::hours(1));
    assert_expires(&mut engine, &mut mixer, expected);
    Ok(())
}

#[test]
fn missing_spring_forward_time_uses_its_next_existing_occurrence() -> Result<()> {
    let start = offset_time("2026-03-08T00:30:00-06:00")?;
    let settings = Settings {
        hour: 2,
        minute: 30,
        reading: true,
    };
    let expected = offset_time("2026-03-08T14:30:00-05:00")?;
    let (mut engine, mut mixer) = start_session(start, settings)?;
    assert_eq!(engine.deadline, Some(expected));
    assert_not_expired(
        &mut engine,
        &mut mixer,
        offset_time("2026-03-08T03:30:00-05:00")?,
    );
    assert_expires(&mut engine, &mut mixer, expected);
    Ok(())
}

#[test]
fn fall_back_respects_alarm_time_already_passed_at_bedtime() -> Result<()> {
    for (start, expected) in [
        ("2026-11-01T00:45:00-05:00", "2026-11-01T01:30:00-05:00"),
        ("2026-11-01T01:45:00-05:00", "2026-11-01T13:30:00-06:00"),
        ("2026-11-01T01:45:00-06:00", "2026-11-01T13:30:00-06:00"),
        ("2026-11-01T01:15:00-06:00", "2026-11-01T01:30:00-06:00"),
    ] {
        let start = offset_time(start)?;
        let expected = offset_time(expected)?;
        let settings = Settings {
            hour: 1,
            minute: 30,
            reading: true,
        };
        let (mut engine, mut mixer) = start_session(start, settings)?;
        assert_eq!(engine.deadline, Some(expected));
        assert_not_expired(&mut engine, &mut mixer, start);
        assert_not_expired(
            &mut engine,
            &mut mixer,
            expected - WallDuration::nanoseconds(1),
        );
        assert_expires(&mut engine, &mut mixer, expected);
    }
    Ok(())
}

#[test]
fn first_one_forty_five_start_does_not_expire_at_repeated_one_thirty() -> Result<()> {
    let start = offset_time("2026-11-01T01:45:00-05:00")?;
    let settings = Settings {
        hour: 1,
        minute: 30,
        reading: true,
    };
    let (mut engine, mut mixer) = start_session(start, settings)?;
    assert_eq!(
        engine.deadline,
        Some(offset_time("2026-11-01T13:30:00-06:00")?)
    );
    assert_not_expired(
        &mut engine,
        &mut mixer,
        offset_time("2026-11-01T01:30:00-06:00")?,
    );
    assert_not_expired(
        &mut engine,
        &mut mixer,
        offset_time("2026-11-01T02:00:00-06:00")?,
    );
    assert_expires(
        &mut engine,
        &mut mixer,
        offset_time("2026-11-01T13:30:00-06:00")?,
    );
    Ok(())
}

fn start_session(start: DateTime<Utc>, settings: Settings) -> Result<(Engine, Mixer)> {
    let mut engine = Engine::new(settings);
    // Follow the app: Play calculates the deadline, then that exact value is
    // passed to the audio thread. Tests never assign a deadline.
    assert_eq!(engine.apply(Command::Play, start), vec![Effect::Begin]);
    let mut mixer = Mixer::new(Arc::from([30, 40]));
    mixer.command(Message::Prepare {
        announcement: Arc::from([1, 2]),
        id: 42,
        deadline: engine.deadline.context("Play did not set a deadline")?,
    });
    mixer.command(Message::Ready {
        reading: engine.settings.reading.then(|| Arc::from([10, 20])),
        id: 42,
    });
    // The app calls start_ready when the audio thread reports Started.
    let mut buffer = [0; 6];
    assert_eq!(mixer.fill(&mut buffer), (true, false));
    assert!(engine.start_ready());
    Ok((engine, mixer))
}

fn assert_not_expired(engine: &mut Engine, mixer: &mut Mixer, now: DateTime<Utc>) {
    let before = (
        engine.state,
        engine.deadline,
        mixer.phase,
        mixer.position,
        mixer.prompt_position,
        mixer.deadline,
        mixer.reading.clone(),
        mixer.prompt.clone(),
        mixer.retired.clone(),
    );
    assert_eq!(mixer.expire(now), None);
    assert!(engine.tick(now).is_empty());
    assert_eq!(
        (
            engine.state,
            engine.deadline,
            mixer.phase,
            mixer.position,
            mixer.prompt_position,
            mixer.deadline,
            mixer.reading.clone(),
            mixer.prompt.clone(),
            mixer.retired.clone(),
        ),
        before,
        "an unexpired session must remain unchanged"
    );
}

fn assert_expires(engine: &mut Engine, mixer: &mut Mixer, now: DateTime<Utc>) {
    assert_eq!(mixer.expire(now), Some(42));
    assert_eq!(engine.tick(now), vec![Effect::Cancel]);
    assert_eq!(engine.state, State::Idle);
    assert_eq!(mixer.phase, Phase::Idle);
    assert!(engine.deadline.is_none());
    assert!(mixer.deadline.is_none());
    assert!(mixer.reading.is_none());
    assert!(mixer.prompt.is_none());
    assert_eq!(mixer.expire(now), None);
    assert!(engine.tick(now).is_empty());
}

#[test]
fn starts_after_midnight_select_the_next_six_thirty() -> Result<()> {
    for (hour, minute, expected_hour) in [(0, 0, 6), (0, 15, 6), (5, 45, 6), (6, 31, 18)] {
        let start = local_time(6, hour, minute)?;
        let expected = local_time(6, expected_hour, 30)?;
        let (mut engine, mut mixer) = start_session(start, Settings::default())?;
        assert_eq!(engine.deadline, Some(expected));
        assert_eq!(mixer.deadline, engine.deadline);
        assert_not_expired(&mut engine, &mut mixer, start);
        assert_not_expired(
            &mut engine,
            &mut mixer,
            expected - WallDuration::nanoseconds(1),
        );
        assert_expires(&mut engine, &mut mixer, expected);
    }
    Ok(())
}

#[test]
fn twelve_after_midnight_means_midnight_or_noon_according_to_start_time() -> Result<()> {
    for (start_hour, start_minute, alarm_minute, expected_hour) in [
        (0, 0, 15, 0),
        (0, 14, 15, 0),
        (0, 15, 15, 12),
        (0, 16, 15, 12),
        (0, 1, 0, 12),
        (0, 45, 30, 12),
    ] {
        let settings = Settings {
            hour: 12,
            minute: alarm_minute,
            reading: true,
        };
        let start = local_time(6, start_hour, start_minute)?;
        let expected = local_time(6, expected_hour, alarm_minute as u32)?;
        let (mut engine, mut mixer) = start_session(start, settings)?;
        assert_eq!(engine.deadline, Some(expected));
        assert_not_expired(&mut engine, &mut mixer, start);
        assert_not_expired(
            &mut engine,
            &mut mixer,
            expected - WallDuration::nanoseconds(1),
        );
        assert_expires(&mut engine, &mut mixer, expected);
    }
    Ok(())
}

#[test]
fn crossing_midnight_does_not_stop_an_overnight_session_early() -> Result<()> {
    let (mut engine, mut mixer) = start_session(local_time(5, 23, 0)?, Settings::default())?;
    let expected = local_time(6, 6, 30)?;
    assert_eq!(engine.deadline, Some(expected));
    for now in [
        local_time(5, 23, 59)?,
        local_time(6, 0, 0)?,
        local_time(6, 0, 1)?,
        expected - WallDuration::nanoseconds(1),
    ] {
        assert_not_expired(&mut engine, &mut mixer, now);
    }
    assert_expires(&mut engine, &mut mixer, expected);
    Ok(())
}

#[test]
fn a_passed_midnight_alarm_does_not_stop_playback_immediately() -> Result<()> {
    let start = local_time(6, 0, 45)?;
    let settings = Settings {
        hour: 12,
        minute: 30,
        reading: false,
    };
    let (mut engine, mut mixer) = start_session(start, settings)?;
    let expected = local_time(6, 12, 30)?;
    assert_eq!(engine.deadline, Some(expected));
    assert_ne!(engine.deadline, Some(local_time(6, 0, 30)?));
    assert_not_expired(&mut engine, &mut mixer, start);
    assert_not_expired(&mut engine, &mut mixer, local_time(6, 1, 0)?);
    assert_not_expired(
        &mut engine,
        &mut mixer,
        expected - WallDuration::nanoseconds(1),
    );
    assert_expires(&mut engine, &mut mixer, expected);
    Ok(())
}

#[test]
fn paused_overnight_session_keeps_its_morning_alarm() -> Result<()> {
    let (mut engine, mut mixer) = start_session(local_time(5, 23, 0)?, Settings::default())?;
    assert_eq!(
        engine.apply(Command::Pause, local_time(5, 23, 30)?),
        vec![Effect::Pause]
    );
    mixer.command(Message::Pause);
    assert_eq!(engine.deadline, Some(local_time(6, 6, 30)?));
    assert_not_expired(&mut engine, &mut mixer, local_time(6, 0, 15)?);
    assert_not_expired(&mut engine, &mut mixer, local_time(6, 6, 15)?);
    assert_expires(&mut engine, &mut mixer, local_time(6, 6, 30)?);
    Ok(())
}

#[test]
fn stopped_overnight_session_does_not_expire_at_the_old_alarm() -> Result<()> {
    let (mut engine, mut mixer) = start_session(local_time(5, 23, 0)?, Settings::default())?;
    assert_eq!(
        engine.apply(Command::Stop, local_time(6, 0, 15)?),
        vec![Effect::Cancel]
    );
    mixer.command(Message::Cancel);
    assert_not_expired(&mut engine, &mut mixer, local_time(6, 6, 30)?);
    Ok(())
}
