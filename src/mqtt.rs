//! MQTT I/O stays off the playback/control threads. Mosquitto owns reconnection.
use crate::control::{Command, Engine, Playback, State};
use anyhow::{ensure, Context, Result};
use serde::Deserialize;
use std::{
    io::{BufRead, BufReader, Read, Write},
    path::Path,
    process::{Child, Command as Process, Stdio},
    sync::{
        mpsc::{self, Receiver, SyncSender},
        Arc, Mutex,
    },
    thread,
    time::{Duration, Instant},
};

const MAX_MESSAGE: u64 = 4096;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    Control(Command),
    Status,
}

pub fn parse_action(payload: &str) -> Option<Action> {
    let name = payload.trim();
    let command = match name {
        "toggle" => Command::Toggle,
        "play" | "start" | "resume" => Command::Play,
        "pause" => Command::Pause,
        "stop" => Command::Stop,
        "volume_up" => Command::VolumeUp,
        "volume_down" => Command::VolumeDown,
        "mute_toggle" | "mute" => Command::MuteToggle,
        "mode" => Command::Mode,
        "alarm_left" => Command::AlarmLeft,
        "alarm_right" => Command::AlarmRight,
        "alarm_up" => Command::AlarmUp,
        "alarm_down" => Command::AlarmDown,
        "current_time" => Command::CurrentTime,
        "bluetooth_pair" => Command::BluetoothPair,
        "status" => return Some(Action::Status),
        _ => return None,
    };
    Some(Action::Control(command))
}

#[derive(Deserialize)]
struct Envelope {
    payload: String,
    retain: u8,
}

fn message(line: &[u8]) -> Option<Action> {
    let envelope: Envelope = serde_json::from_slice(line).ok()?;
    if envelope.retain != 0 {
        return None;
    }
    parse_action(&envelope.payload)
}

pub fn state(engine: &Engine) -> String {
    let (phase, paused) = match engine.state {
        State::Idle => ("idle", false),
        State::Starting => ("starting", false),
        State::Reading => ("reading", false),
        State::Noise => ("noise", false),
        State::Paused(Playback::Reading) => ("reading", true),
        State::Paused(Playback::Noise) => ("noise", true),
    };
    serde_json::json!({
        "phase": phase, "paused": paused,
        "wake_hour": engine.settings.hour, "wake_minute": engine.settings.minute,
        "reading": engine.settings.reading, "volume": engine.volume,
        "muted": engine.muted, "deadline": engine.deadline,
    })
    .to_string()
}

type ProcessSlot = Arc<Mutex<Option<Child>>>;

pub struct Client {
    actions: Receiver<(Instant, Action)>,
    states: SyncSender<String>,
    subscriber: ProcessSlot,
    publisher: ProcessSlot,
    stop: Arc<std::sync::atomic::AtomicBool>,
}

fn terminate(slot: &ProcessSlot) {
    if let Ok(mut guard) = slot.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

impl Drop for Client {
    fn drop(&mut self) {
        self.stop.store(true, std::sync::atomic::Ordering::Release);
        terminate(&self.subscriber);
        terminate(&self.publisher);
    }
}

fn process(program: &str, directory: &Path) -> Process {
    let mut command = Process::new(program);
    // Both clients read private config files; credentials never enter argv/logs.
    command
        .env("XDG_CONFIG_HOME", directory)
        .stderr(Stdio::null());
    command
}

impl Client {
    pub fn start(directory: &Path) -> Result<Self> {
        let directory = directory
            .canonicalize()
            .context("MQTT configuration directory")?;
        for name in ["mosquitto_sub", "mosquitto_pub"] {
            ensure!(
                directory.join(name).is_file(),
                "MQTT client configuration missing"
            );
        }
        let prefix = std::fs::read_to_string(directory.join("topic"))
            .context("MQTT topic configuration missing")?;
        let prefix = prefix.trim();
        ensure!(
            !prefix.is_empty()
                && prefix.len() <= 128
                && prefix
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"/_-".contains(&b))
                && !prefix.ends_with('/'),
            "invalid MQTT topic prefix"
        );
        let action_topic = format!("{prefix}/action");
        let state_topic = format!("{prefix}/state");
        let (action_tx, actions) = mpsc::sync_channel(32);
        let (states, state_rx) = mpsc::sync_channel::<String>(1);
        let subscriber: ProcessSlot = Arc::new(Mutex::new(None));
        let publisher: ProcessSlot = Arc::new(Mutex::new(None));
        let stop = Arc::new(std::sync::atomic::AtomicBool::new(false));
        let client = Self {
            actions,
            states,
            subscriber,
            publisher,
            stop,
        };

        let slot = client.subscriber.clone();
        let stopped = client.stop.clone();
        let config = directory.clone();
        thread::Builder::new()
            .name("shush-mqtt-in".into())
            .spawn(move || {
                while !stopped.load(std::sync::atomic::Ordering::Acquire) {
                    let child = process("mosquitto_sub", &config)
                        .args([
                            "-t",
                            &action_topic,
                            "-q",
                            "0",
                            "-R",
                            "-F",
                            "%j",
                            "-V",
                            "mqttv5",
                            "--retain-as-published",
                            "-D",
                            "connect",
                            "maximum-packet-size",
                            "4096",
                        ])
                        .stdin(Stdio::null())
                        .stdout(Stdio::piped())
                        .spawn();
                    if let Ok(mut child) = child {
                        let output = child.stdout.take();
                        if let Ok(mut guard) = slot.lock() {
                            *guard = Some(child);
                        }
                        if let Some(output) = output {
                            let mut reader = BufReader::new(output);
                            loop {
                                let mut line = Vec::new();
                                // Never allocate an unbounded buffer for network data.
                                let count = reader
                                    .by_ref()
                                    .take(MAX_MESSAGE + 1)
                                    .read_until(b'\n', &mut line);
                                if count.is_err() || line.is_empty() {
                                    break;
                                }
                                if line.len() as u64 > MAX_MESSAGE || !line.ends_with(b"\n") {
                                    break;
                                }
                                if let Some(action) = message(&line) {
                                    let _ = action_tx.try_send((Instant::now(), action));
                                }
                                if stopped.load(std::sync::atomic::Ordering::Acquire) {
                                    break;
                                }
                            }
                        }
                        terminate(&slot);
                    }
                    thread::sleep(Duration::from_secs(5));
                }
            })?;

        let slot = client.publisher.clone();
        let stopped = client.stop.clone();
        thread::Builder::new()
            .name("shush-mqtt-out".into())
            .spawn(move || {
                let mut latest = None;
                while !stopped.load(std::sync::atomic::Ordering::Acquire) {
                    let child = process("mosquitto_pub", &directory)
                        .args(["-t", &state_topic, "-q", "0", "-r", "-l"])
                        .stdin(Stdio::piped())
                        .stdout(Stdio::null())
                        .spawn();
                    if let Ok(mut child) = child {
                        let input = child.stdin.take();
                        if let Ok(mut guard) = slot.lock() {
                            *guard = Some(child);
                        }
                        if let Some(mut input) = input {
                            let mut publish = latest.is_some();
                            loop {
                                if stopped.load(std::sync::atomic::Ordering::Acquire) {
                                    break;
                                }
                                // Re-publish the latest known state after reconnect/start failure.
                                if let Ok(value) = state_rx.recv_timeout(Duration::from_secs(1)) {
                                    latest = Some(value);
                                    publish = true;
                                }
                                // Prefer the newest queued snapshot.
                                while let Ok(value) = state_rx.try_recv() {
                                    latest = Some(value);
                                    publish = true;
                                }
                                if publish {
                                    if let Some(value) = &latest {
                                        if writeln!(input, "{value}")
                                            .and_then(|()| input.flush())
                                            .is_err()
                                        {
                                            break;
                                        }
                                    }
                                    publish = false;
                                }
                                let exited = slot
                                    .lock()
                                    .ok()
                                    .and_then(|mut guard| {
                                        guard
                                            .as_mut()
                                            .and_then(|child| child.try_wait().ok().flatten())
                                    })
                                    .is_some();
                                if exited {
                                    break;
                                }
                            }
                        }
                        terminate(&slot);
                    }
                    thread::sleep(Duration::from_secs(5));
                }
            })?;
        Ok(client)
    }

    pub fn receive(&self) -> Option<Action> {
        let (received, action) = self.actions.try_recv().ok()?;
        // Drop commands delayed by a busy controller rather than replaying them.
        (received.elapsed() < Duration::from_secs(2)).then_some(action)
    }

    pub fn publish(&self, state: String) -> bool {
        self.states.try_send(state).is_ok()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::settings::Settings;

    #[test]
    fn only_explicit_actions_are_accepted_and_retained_commands_are_ignored() {
        assert_eq!(parse_action("stop"), Some(Action::Control(Command::Stop)));
        assert_eq!(parse_action("resume"), Some(Action::Control(Command::Play)));
        assert_eq!(parse_action("status"), Some(Action::Status));
        for payload in ["reboot", "stop\nplay", "{\"action\":\"play\"}", "", "PLAY"] {
            assert_eq!(parse_action(payload), None);
        }
        assert_eq!(
            message(br#"{"payload":"stop","retain":0}"#),
            parse_action("stop")
        );
        assert_eq!(message(br#"{"payload":"play","retain":1}"#), None);
        assert_eq!(message(br#"{"payload":"stop\nplay","retain":0}"#), None);
        assert_eq!(message(b"invalid"), None);
    }

    #[test]
    fn published_state_reports_settings_and_the_actual_fixed_deadline() {
        let mut engine = Engine::new(Settings::default());
        let now = chrono::Utc::now();
        engine.apply(Command::Play, now);
        engine.start_ready();
        engine.apply(Command::Pause, now);
        let deadline = engine.deadline;
        engine.apply(Command::AlarmUp, now);
        let value: serde_json::Value = serde_json::from_str(&state(&engine)).unwrap_or_default();
        assert_eq!(value["paused"], true);
        assert_eq!(value["wake_hour"], engine.settings.hour);
        assert_eq!(value["deadline"], serde_json::json!(deadline));
        engine.apply(Command::Stop, now);
        let value: serde_json::Value = serde_json::from_str(&state(&engine)).unwrap_or_default();
        assert_eq!(value["phase"], "idle");
        assert!(value["deadline"].is_null());
    }

    #[test]
    #[ignore = "requires local mosquitto and mosquitto-clients binaries"]
    fn live_broker_controls_state_retention_and_reconnection() -> Result<()> {
        struct Broker(Child);
        impl Drop for Broker {
            fn drop(&mut self) {
                let _ = self.0.kill();
                let _ = self.0.wait();
            }
        }
        struct Folder(std::path::PathBuf);
        impl Drop for Folder {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let port = listener.local_addr()?.port().to_string();
        drop(listener);
        let folder =
            Folder(std::env::temp_dir().join(format!("shush-mqtt-test-{}", std::process::id())));
        std::fs::create_dir_all(&folder.0)?;
        let broker_config = folder.0.join("broker.conf");
        std::fs::write(
            &broker_config,
            format!("listener {port} 127.0.0.1\nallow_anonymous true\n"),
        )?;
        let start_broker = || -> Result<Broker> {
            let broker = Broker(
                Process::new("mosquitto")
                    .arg("-c")
                    .arg(&broker_config)
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .spawn()?,
            );
            let until = Instant::now() + Duration::from_secs(5);
            while std::net::TcpStream::connect(format!("127.0.0.1:{port}")).is_err() {
                ensure!(Instant::now() < until, "test broker did not start");
                thread::sleep(Duration::from_millis(25));
            }
            Ok(broker)
        };
        let broker = start_broker()?;
        let client_config = format!("-h 127.0.0.1\n-p {port}\n");
        for name in ["mosquitto_sub", "mosquitto_pub"] {
            std::fs::write(folder.0.join(name), &client_config)?;
        }
        std::fs::write(folder.0.join("topic"), "test-shush")?;
        let send = |payload: &str, retained: bool| -> Result<()> {
            let mut command = process("mosquitto_pub", &folder.0);
            command.args(["-t", "test-shush/action", "-m", payload]);
            if retained {
                command.arg("-r");
            }
            ensure!(command.status()?.success(), "test publish failed");
            Ok(())
        };
        send("play", true)?;
        let client = Client::start(&folder.0)?;
        thread::sleep(Duration::from_millis(500));
        assert_eq!(
            client.receive(),
            None,
            "retained startup action was replayed"
        );
        send("pause", true)?;
        thread::sleep(Duration::from_millis(250));
        assert_eq!(client.receive(), None, "live retained action was accepted");
        send("stop\nplay", false)?;
        thread::sleep(Duration::from_millis(100));
        assert_eq!(client.receive(), None, "multiline command was accepted");

        let receive_action = |payload: &str, expected: Action| -> Result<()> {
            let until = Instant::now() + Duration::from_secs(15);
            loop {
                send(payload, false)?;
                thread::sleep(Duration::from_millis(150));
                if let Some(action) = client.receive() {
                    assert_eq!(action, expected);
                    break;
                }
                ensure!(Instant::now() < until, "MQTT action not received");
            }
            Ok(())
        };
        receive_action("current_time", Action::Control(Command::CurrentTime))?;
        let snapshot = state(&Engine::new(Settings::default()));
        client.publish(snapshot.clone());
        let received = process("mosquitto_sub", &folder.0)
            .args(["-t", "test-shush/state", "-C", "1", "-W", "5"])
            .output()?;
        ensure!(received.status.success(), "MQTT state not received");
        assert_eq!(String::from_utf8(received.stdout)?.trim(), snapshot);
        // A new subscriber must get the retained state, without another publish.
        let retained = process("mosquitto_sub", &folder.0)
            .args(["-t", "test-shush/state", "-C", "1", "-W", "5"])
            .output()?;
        ensure!(retained.status.success(), "MQTT state was not retained");
        assert_eq!(String::from_utf8(retained.stdout)?.trim(), snapshot);
        drop(broker);
        thread::sleep(Duration::from_millis(250));
        let _broker = start_broker()?;
        receive_action("stop", Action::Control(Command::Stop))?;
        // Drop kills both clients before temporary configuration is removed.
        drop(client);
        Ok(())
    }
}
