#![forbid(unsafe_code)]
mod audio;
mod bluetooth;
mod control;
mod hardware_volume;
mod media;
mod mqtt;
mod preload;
mod settings;

use anyhow::{bail, ensure, Context, Result};
use chrono::Utc;
use control::{Command, Effect, Engine, State};
use std::os::unix::fs::OpenOptionsExt;
use std::{
    path::PathBuf,
    sync::atomic::Ordering,
    thread,
    time::{Duration, Instant},
};

struct Options {
    command: String,
    root: PathBuf,
    settings: PathBuf,
    input: Option<PathBuf>,
    audio: String,
    mqtt_config: Option<PathBuf>,
}
impl Options {
    fn parse() -> Result<Self> {
        let mut args = std::env::args().skip(1);
        let command = args.next().unwrap_or_else(|| "help".into());
        let mut options = Self {
            command,
            root: PathBuf::from("."),
            settings: PathBuf::from("settings.json"),
            input: None,
            audio: "default".into(),
            mqtt_config: std::env::var_os("CREDENTIALS_DIRECTORY")
                .map(PathBuf::from)
                .filter(|path| path.is_dir()),
        };
        while let Some(flag) = args.next() {
            let value = args
                .next()
                .with_context(|| format!("missing value for {flag}"))?;
            match flag.as_str() {
                "--root" => options.root = value.into(),
                "--settings" => options.settings = value.into(),
                "--input" => options.input = Some(value.into()),
                "--audio" => options.audio = value,
                "--mqtt-config" => options.mqtt_config = Some(value.into()),
                _ => bail!("unknown flag {flag}"),
            }
        }
        Ok(options)
    }
}
fn main() {
    if let Err(error) = entry() {
        eprintln!("shush: {error:#}");
        std::process::exit(1);
    }
}
fn entry() -> Result<()> {
    let options = Options::parse()?;
    if matches!(options.command.as_str(), "help" | "--help" | "-h") {
        println!("shush run --root PATH --settings PATH --input /dev/input/by-id/... --audio plughw:CARD=Device,DEV=0 [--mqtt-config DIRECTORY]\nshush check --root PATH\nshush validate-media --root PATH");
        return Ok(());
    }
    if options.command == "clock-error" {
        // Boot diagnostics must work without the clock, receiver, schedule,
        // settings, or main audio worker. aplay drains the complete WAV.
        let prompt = options.root.join("voice/clock-network-error.wav");
        media::wav(&prompt).context("validating clock failure prompt")?;
        hardware_volume::initialize(&options.audio)?;
        let status = std::process::Command::new("/usr/bin/aplay")
            .args(["--quiet", "--device", &options.audio])
            .arg(prompt)
            .status()
            .context("playing clock failure prompt")?;
        ensure!(status.success(), "clock failure prompt playback failed");
        return Ok(());
    }
    ensure!(
        matches!(options.command.as_str(), "run" | "check" | "validate-media"),
        "unknown command"
    );
    let schedule = media::Schedule::load(&options.root)?;
    let noise = media::wav(&options.root.join("Pink_Noise.wav"))?;
    let voices = media::VoiceBank::load(&options.root)?;
    if options.command == "check" {
        settings::load(&options.settings)?;
        println!("Schedule, referenced files, noise, 771 voice prompts, and settings checked.");
        return Ok(());
    }
    if options.command == "validate-media" {
        return media::validate_all(&options.root, &schedule);
    }
    run(options, schedule, noise, voices)
}
fn run(
    options: Options,
    schedule: media::Schedule,
    noise: media::Samples,
    voices: media::VoiceBank,
) -> Result<()> {
    clock_ready()?;
    // Decode before opening the output or accepting commands: startup stays silent.
    let mut daily = preload::DailyPlaylist::new(Utc::now(), |day| {
        media::playlist(&options.root, schedule.day(day)?)
    })?;
    let input = options
        .input
        .as_ref()
        .context("run requires --input with a persistent receiver device path")?;
    let input_file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NONBLOCK)
        .open(input)
        .context("opening HID receiver")?;
    let mut device = evdev::raw_stream::RawDevice::try_from(input_file)?;
    device
        .grab()
        .context("grabbing the remote receiver exclusively")?;
    let mut engine = Engine::new(settings::load(&options.settings)?);
    let output = audio::Output::open(&options.audio, noise)?;
    let mut bluetooth_control = bluetooth::Control::new();
    bluetooth_control.update(engine.state);
    let mqtt = options
        .mqtt_config
        .as_deref()
        .and_then(|path| match mqtt::Client::start(path) {
            Ok(client) => Some(client),
            Err(error) => {
                eprintln!("shush: MQTT unavailable: {error:#}");
                None
            }
        });
    let mut last_mqtt_state = String::new();
    let mut last_mqtt_publish = Instant::now() - Duration::from_secs(30);
    let mut next_preload_attempt = Instant::now();
    let mut generation = 0u64;
    let mut last_adjustment = Instant::now() - Duration::from_secs(1);
    let mut last_gain = (engine.volume, engine.muted);
    let mut speech: Option<media::Samples> = None;
    let mut last_settings = engine.settings.clone();
    let mut input_dropped = false;
    let mut input_failed = false;
    // All media, settings, clock, input and audio are ready at this point.
    let ready = engine.apply(Command::CurrentTime, Utc::now());
    handle_effects(
        ready,
        &mut engine,
        &output,
        &options,
        &daily,
        &mut generation,
        &mut speech,
        &mut last_settings,
        &voices,
    )?;
    if let Some(prompt) = speech.take() {
        output.send(audio::Message::Prompt(prompt))?;
    }
    // No playback is restored automatically at startup.
    loop {
        let now = Utc::now();
        if engine.state == State::Idle
            && daily.due(now)
            && Instant::now() >= next_preload_attempt
            && output.is_silent()?
        {
            // Synchronous by design: no remote, MQTT, prompt or Bluetooth command can
            // start sound between the silence acknowledgment and the end of decoding.
            if let Err(error) = daily.refresh(now, true, |day| {
                media::playlist(&options.root, schedule.day(day)?)
            }) {
                eprintln!("shush: daily preload failed: {error:#}");
                next_preload_attempt = Instant::now() + Duration::from_secs(60);
            }
        }
        handle_effects(
            engine.tick(now),
            &mut engine,
            &output,
            &options,
            &daily,
            &mut generation,
            &mut speech,
            &mut last_settings,
            &voices,
        )?;
        while let Ok(event) = output.events.try_recv() {
            match event {
                audio::Event::Started(id) if id == generation => {
                    engine.start_ready();
                }
                audio::Event::ReadingFinished(id) if id == generation => {
                    engine.reading_finished();
                }
                audio::Event::DeadlineReached(id) if id == generation => {
                    engine.abort_start();
                    generation = generation.wrapping_add(1);
                }
                audio::Event::Retired(samples) => drop(samples),
                audio::Event::Fault(error) => bail!("{error}"),
                _ => {}
            }
        }
        if output.stop.load(Ordering::Acquire) {
            bail!("audio output thread stopped");
        }
        // Raw evdev reads a bounded batch. Never replay synthetic presses after dropped input.
        if !input_failed {
            match device.fetch_events() {
                Ok(events) => {
                    for event in events {
                        if event.event_type() == evdev::EventType::SYNCHRONIZATION {
                            if event.code() == 3 {
                                input_dropped = true;
                            } else if event.code() == 0 {
                                input_dropped = false;
                            }
                            continue;
                        }
                        if input_dropped {
                            continue;
                        }
                        if event.event_type() != evdev::EventType::KEY {
                            continue;
                        }
                        let Some(command) = Command::from_key(event.code(), event.value()) else {
                            continue;
                        };
                        if event.value() == 2
                            && last_adjustment.elapsed() < Duration::from_millis(180)
                        {
                            continue;
                        }
                        if event.value() == 2 {
                            last_adjustment = Instant::now();
                        }
                        let effects = engine.apply(command, Utc::now());
                        handle_effects(
                            effects,
                            &mut engine,
                            &output,
                            &options,
                            &daily,
                            &mut generation,
                            &mut speech,
                            &mut last_settings,
                            &voices,
                        )?;
                    }
                }
                Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(error) if error.kind() == std::io::ErrorKind::Interrupted => {}
                Err(error) => {
                    // A control-device fault must not stop independent audio or its deadline.
                    eprintln!(
                        "shush: HID receiver unavailable: {error}; active playback continues"
                    );
                    input_failed = true;
                }
            }
        }
        // Coalesce adjustment announcements within a poll instead of building a speech queue.
        let mut status_requested = false;
        if let Some(client) = &mqtt {
            // Bound work per poll so network traffic cannot starve remote input.
            for _ in 0..8 {
                let Some(action) = client.receive() else {
                    break;
                };
                match action {
                    mqtt::Action::Status => status_requested = true,
                    mqtt::Action::Control(command) => {
                        let effects = engine.apply(command, Utc::now());
                        handle_effects(
                            effects,
                            &mut engine,
                            &output,
                            &options,
                            &daily,
                            &mut generation,
                            &mut speech,
                            &mut last_settings,
                            &voices,
                        )?;
                    }
                }
            }
            let state = mqtt::state(&engine);
            let publish_due = state != last_mqtt_state
                || status_requested
                || last_mqtt_publish.elapsed() >= Duration::from_secs(30);
            if publish_due && client.publish(state.clone()) {
                last_mqtt_state = state;
                last_mqtt_publish = Instant::now();
            }
        }
        if let Some(prompt) = speech.take() {
            output.send(audio::Message::Prompt(prompt))?;
        }
        let gain = (engine.volume, engine.muted);
        bluetooth_control.update(engine.state);
        if gain != last_gain {
            output.send(audio::Message::Gain(gain.0, gain.1))?;
            last_gain = gain;
        }
        thread::sleep(Duration::from_millis(10));
    }
}
#[allow(clippy::too_many_arguments)]
fn handle_effects(
    effects: Vec<Effect>,
    engine: &mut Engine,
    output: &audio::Output,
    options: &Options,
    daily: &preload::DailyPlaylist,
    generation: &mut u64,
    speech: &mut Option<media::Samples>,
    last_settings: &mut settings::Settings,
    voices: &media::VoiceBank,
) -> Result<()> {
    for effect in effects {
        match effect {
            Effect::Begin => {
                bluetooth::send(b"busy");
                *speech = None;
                output.send(audio::Message::Cancel)?;
                *generation = generation.wrapping_add(1);
                let id = *generation;
                let settings = &engine.settings;
                let reading = settings.reading.then(|| daily.samples());
                let deadline = engine.deadline.context("starting without deadline")?;
                let announcement = voices.time("wake", settings.hour, settings.minute)?;
                let total =
                    announcement.len() + reading.as_ref().map_or(0, |samples| samples.len());
                let remaining = deadline
                    .signed_duration_since(Utc::now())
                    .num_milliseconds();
                if remaining <= 0 || total as u64 * 1000 / media::RATE as u64 >= remaining as u64 {
                    engine.abort_start();
                    *speech = Some(voices.get("error.wav")?);
                    continue;
                }
                // Both messages reference resident PCM. Play performs no media I/O or decoding.
                output.send(audio::Message::Prepare {
                    announcement,
                    id,
                    deadline,
                })?;
                output.send(audio::Message::Ready { reading, id })?;
            }
            Effect::Pause => output.send(audio::Message::Pause)?,
            Effect::Resume => {
                *speech = None;
                output.send(audio::Message::Resume)?;
            }
            Effect::Cancel => {
                bluetooth::send(b"stop");
                *generation = generation.wrapping_add(1);
                *speech = None;
                output.send(audio::Message::Cancel)?;
            }
            Effect::SpeakMode(reading) => {
                *speech = Some(voices.get(if reading {
                    "mode-reading.wav"
                } else {
                    "mode-noise.wav"
                })?);
            }
            Effect::SpeakTime(hour, minute) => {
                *speech = Some(voices.time("wake", hour, minute)?);
            }
            Effect::SpeakCurrentTime(hour, minute) => {
                *speech = Some(voices.time("current", hour, minute)?);
            }
            Effect::SaveSettings => {
                if let Err(error) = settings::save(&options.settings, &engine.settings) {
                    engine.settings = last_settings.clone();
                    eprintln!("shush: settings not changed: {error:#}");
                    *speech = Some(voices.get("error.wav")?);
                    // Do not announce settings that were not saved.
                    break;
                }
                *last_settings = engine.settings.clone();
            }
            Effect::Unavailable => {
                *speech = Some(voices.get("error.wav")?);
            }
            Effect::BluetoothPair => {
                bluetooth::send(b"idle");
                bluetooth::send(b"pair");
                output.send(audio::Message::BluetoothEnabled(true))?;
            }
        }
    }
    Ok(())
}

/// Query the OS clock state once before accepting remote commands. The service
/// also pulls in systemd-time-wait-sync before starting this process.
fn clock_ready() -> Result<()> {
    use std::process::{Command as ProcessCommand, Stdio};
    let mut child = ProcessCommand::new("timedatectl")
        .args(["show", "--property=NTPSynchronized", "--value"])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .context("querying clock synchronization with timedatectl")?;
    let end = Instant::now() + Duration::from_secs(2);
    loop {
        if child.try_wait()?.is_some() {
            let output = child.wait_with_output()?;
            ensure!(output.status.success() && output.stdout==b"yes\n","clock is not synchronized; enable OS network time synchronization before starting shush");
            return Ok(());
        }
        if Instant::now() >= end {
            let _ = child.kill();
            let _ = child.wait();
            bail!("clock synchronization query timed out");
        }
        thread::sleep(Duration::from_millis(20));
    }
}
