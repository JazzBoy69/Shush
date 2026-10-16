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

fn discovery(prefix: &str) -> (String, String) {
    // Encode the complete configured topic prefix: stable across boots, and no
    // collisions between prefixes such as "room/shush" and "room_shush".
    let id = format!(
        "shush_{}",
        prefix
            .bytes()
            .map(|b| format!("{b:02x}"))
            .collect::<String>()
    );
    let mut components = serde_json::Map::new();
    for (action, name) in [
        ("play", "Play"),
        ("pause", "Pause"),
        ("stop", "Stop"),
        ("volume_up", "Volume up"),
        ("volume_down", "Volume down"),
        ("mute_toggle", "Toggle mute"),
        ("mode", "Change reading mode"),
        ("alarm_left", "Alarm hour earlier"),
        ("alarm_right", "Alarm hour later"),
        ("alarm_up", "Alarm minute later"),
        ("alarm_down", "Alarm minute earlier"),
        ("current_time", "Announce time"),
        ("bluetooth_pair", "Bluetooth pairing"),
    ] {
        components.insert(action.into(), serde_json::json!({
            "platform": "button", "name": name, "unique_id": format!("{id}_{action}"),
            "command_topic": format!("{prefix}/action"), "payload_press": action, "retain": false,
        }));
    }
    for (key, name, template, binary) in [
        ("phase", "Playback", "{{ value_json.phase }}", false),
        ("playing", "Playing", "{{ 'ON' if value_json.phase in ['reading', 'noise'] and not value_json.paused else 'OFF' }}", true),
        ("paused", "Paused", "{{ 'ON' if value_json.paused else 'OFF' }}", true),
        ("muted", "Muted", "{{ 'ON' if value_json.muted else 'OFF' }}", true),
        ("reading", "Reading enabled", "{{ 'ON' if value_json.reading else 'OFF' }}", true),
        ("volume", "Volume", "{{ value_json.volume }}", false),
        ("alarm", "Alarm time", "{{ '%02d:%02d' | format(value_json.wake_hour, value_json.wake_minute) }}", false),
    ] {
        let mut component = serde_json::json!({
            "platform": if binary { "binary_sensor" } else { "sensor" },
            "name": name, "unique_id": format!("{id}_{key}"),
            "state_topic": format!("{prefix}/state"), "value_template": template,
            "expire_after": 90,
        });
        if key == "volume" { component["unit_of_measurement"] = "%".into(); }
        components.insert(key.into(), component);
    }
    let config = serde_json::json!({
        "device": { "identifiers": [id], "name": "Shush", "model": "Audio alarm appliance",
                    "sw_version": env!("CARGO_PKG_VERSION") },
        "origin": { "name": "Shush", "sw_version": env!("CARGO_PKG_VERSION") },
        "availability": [{ "topic": format!("{prefix}/availability") }],
        "components": components,
    });
    (
        format!("homeassistant/device/{id}/config"),
        config.to_string(),
    )
}

fn publish_discovery(directory: &Path, topic: &str, payload: &str) -> bool {
    let Ok(mut child) = process("mosquitto_pub", directory)
        .args(["-t", topic, "-m", payload, "-q", "1", "-r"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .spawn()
    else {
        return false;
    };
    let deadline = Instant::now() + Duration::from_secs(3);
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return status.success(),
            Ok(None) if Instant::now() < deadline => thread::sleep(Duration::from_millis(25)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return false;
            }
        }
    }
}

type ProcessSlot = Arc<Mutex<Option<Child>>>;

pub struct Client {
    actions: Receiver<(Instant, Action)>,
    states: SyncSender<String>,
    subscriber: ProcessSlot,
    publisher: ProcessSlot,
    availability: ProcessSlot,
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
        terminate(&self.availability);
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
        let availability_topic = format!("{prefix}/availability");
        let (discovery_topic, discovery_payload) = discovery(prefix);
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
            availability: Arc::new(Mutex::new(None)),
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
        let availability_slot = client.availability.clone();
        let stopped = client.stop.clone();
        thread::Builder::new()
            .name("shush-mqtt-out".into())
            .spawn(move || {
                let mut latest = None;
                let mut next_discovery = Instant::now();
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
                            let available = process("mosquitto_pub", &directory)
                                .args([
                                    "-t",
                                    &availability_topic,
                                    "-q",
                                    "1",
                                    "-r",
                                    "-l",
                                    "-k",
                                    "15",
                                    "--will-topic",
                                    &availability_topic,
                                    "--will-payload",
                                    "offline",
                                    "--will-qos",
                                    "1",
                                    "--will-retain",
                                ])
                                .stdin(Stdio::piped())
                                .stdout(Stdio::null())
                                .spawn();
                            let Ok(mut available) = available else {
                                terminate(&slot);
                                thread::sleep(Duration::from_secs(5));
                                continue;
                            };
                            let Some(mut availability_input) = available.stdin.take() else {
                                let _ = available.kill();
                                let _ = available.wait();
                                terminate(&slot);
                                continue;
                            };
                            if let Ok(mut guard) = availability_slot.lock() {
                                *guard = Some(available);
                            }
                            let mut publish = latest.is_some();
                            loop {
                                if stopped.load(std::sync::atomic::Ordering::Acquire) {
                                    break;
                                }
                                if Instant::now() >= next_discovery {
                                    let sent = publish_discovery(
                                        &directory,
                                        &discovery_topic,
                                        &discovery_payload,
                                    );
                                    next_discovery = Instant::now()
                                        + Duration::from_secs(if sent { 60 } else { 5 });
                                    if stopped.load(std::sync::atomic::Ordering::Acquire) {
                                        break;
                                    }
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
                                            .and_then(|()| writeln!(availability_input, "online"))
                                            .and_then(|()| availability_input.flush())
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
                                let availability_exited = availability_slot
                                    .lock()
                                    .ok()
                                    .and_then(|mut guard| {
                                        guard
                                            .as_mut()
                                            .and_then(|child| child.try_wait().ok().flatten())
                                    })
                                    .is_some();
                                if exited || availability_exited {
                                    break;
                                }
                            }
                        }
                        terminate(&slot);
                        terminate(&availability_slot);
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
    fn discovery_exposes_safe_controls_and_live_playback_under_one_stable_device() {
        let (topic, payload) = discovery("room/shush");
        let config: serde_json::Value = serde_json::from_str(&payload).unwrap_or_default();
        assert_eq!(discovery("room/shush").0, topic);
        assert_ne!(discovery("room_shush").0, topic);
        assert!(topic.starts_with("homeassistant/device/"));
        assert!(topic.ends_with("/config"));
        assert_eq!(config["device"]["name"], "Shush");
        assert_eq!(
            config["availability"][0]["topic"],
            "room/shush/availability"
        );
        let Some(components) = config["components"].as_object() else {
            panic!("missing components");
        };
        let mut ids = std::collections::HashSet::new();
        for entity in components.values() {
            assert!(ids.insert(entity["unique_id"].as_str()));
            if entity["platform"] == "button" {
                assert_eq!(entity["retain"], false);
                assert_eq!(entity["command_topic"], "room/shush/action");
                assert!(entity["payload_press"]
                    .as_str()
                    .and_then(parse_action)
                    .is_some());
            } else {
                assert_eq!(entity["state_topic"], "room/shush/state");
                assert_eq!(entity["expire_after"], 90);
            }
        }
        assert_eq!(components["playing"]["platform"], "binary_sensor");
        assert!(components["playing"]["value_template"]
            .as_str()
            .is_some_and(|s| s.contains("not value_json.paused")));
        assert_eq!(components["volume"]["unit_of_measurement"], "%");
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
        let (discovery_topic, _) = discovery("test-shush");
        let discovered = process("mosquitto_sub", &folder.0)
            .args(["-t", &discovery_topic, "-C", "1", "-W", "5"])
            .output()?;
        ensure!(
            discovered.status.success(),
            "retained discovery not received"
        );
        let config: serde_json::Value = serde_json::from_slice(&discovered.stdout)?;
        assert_eq!(config["device"]["name"], "Shush");
        assert_eq!(config["components"]["play"]["payload_press"], "play");
        let online = process("mosquitto_sub", &folder.0)
            .args(["-t", "test-shush/availability", "-C", "1", "-W", "5"])
            .output()?;
        assert_eq!(String::from_utf8(online.stdout)?.trim(), "online");
        let playback_listener = process("mosquitto_sub", &folder.0)
            .args(["-t", "test-shush/state", "-R", "-C", "1", "-W", "5"])
            .stdout(Stdio::piped())
            .spawn()?;
        thread::sleep(Duration::from_millis(150));
        let mut playing = Engine::new(Settings::default());
        playing.apply(Command::Play, chrono::Utc::now());
        playing.start_ready();
        let reading = state(&playing);
        assert!(client.publish(reading.clone()));
        let update = playback_listener.wait_with_output()?;
        ensure!(update.status.success(), "live playback update not received");
        let status: serde_json::Value = serde_json::from_slice(&update.stdout)?;
        assert_eq!(status["phase"], "reading");
        assert_eq!(status["paused"], false);
        // A new subscriber must get the retained state, without another publish.
        let retained = process("mosquitto_sub", &folder.0)
            .args(["-t", "test-shush/state", "-C", "1", "-W", "5"])
            .output()?;
        ensure!(retained.status.success(), "MQTT state was not retained");
        assert_eq!(String::from_utf8(retained.stdout)?.trim(), reading);
        drop(broker);
        thread::sleep(Duration::from_millis(250));
        let _broker = start_broker()?;
        receive_action("stop", Action::Control(Command::Stop))?;
        // Broker lost its retained database: discovery must be republished by
        // the running appliance, without requiring an appliance restart.
        let rediscovered = process("mosquitto_sub", &folder.0)
            .args(["-t", &discovery_topic, "-C", "1", "-W", "70"])
            .output()?;
        ensure!(
            rediscovered.status.success(),
            "discovery did not recover after broker restart"
        );
        drop(client);
        // Killing the availability connection must send its retained last will.
        thread::sleep(Duration::from_millis(250));
        let offline = process("mosquitto_sub", &folder.0)
            .args(["-t", "test-shush/availability", "-C", "1", "-W", "5"])
            .output()?;
        ensure!(
            offline.status.success(),
            "offline availability not received"
        );
        assert_eq!(String::from_utf8(offline.stdout)?.trim(), "offline");
        Ok(())
    }
}
