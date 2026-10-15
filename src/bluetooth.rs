//! Bluetooth control and bounded, nonblocking PCM input. USB audio stays owned by Shush.
use crate::control::State;
use std::{
    fs::{File, OpenOptions},
    io::{ErrorKind, Read},
    os::unix::{
        fs::{FileTypeExt, OpenOptionsExt},
        net::UnixDatagram,
    },
    time::{Duration, Instant},
};

const DIRECTORY: &str = "/run/shush-bluetooth";
const CAPACITY: usize = 8192;
const PREBUFFER: usize = 1323; // 30 ms at 44.1 kHz.

pub fn send(message: &[u8]) {
    if let Ok(socket) = UnixDatagram::unbound() {
        let _ = socket.set_nonblocking(true);
        let _ = socket.send_to(message, format!("{DIRECTORY}/control.sock"));
    }
}

pub struct Control {
    last_idle: Option<bool>,
    last_sent: Instant,
}

impl Control {
    pub fn new() -> Self {
        Self {
            last_idle: None,
            last_sent: Instant::now(),
        }
    }
    pub fn update(&mut self, state: State) {
        let idle = state == State::Idle;
        if self.last_idle != Some(idle) || self.last_sent.elapsed() >= Duration::from_secs(1) {
            send(if idle { b"idle" } else { b"busy" });
            self.last_idle = Some(idle);
            self.last_sent = Instant::now();
        }
    }
}

pub struct Input {
    file: File,
    samples: [[i16; 2]; CAPACITY],
    head: usize,
    len: usize,
    fraction: f64,
    playing: bool,
    carry: [u8; 4],
    carry_len: usize,
}

impl Input {
    fn new(file: File) -> Self {
        Self {
            file,
            samples: [[0; 2]; CAPACITY],
            head: 0,
            len: 0,
            fraction: 0.0,
            playing: false,
            carry: [0; 4],
            carry_len: 0,
        }
    }
    pub fn open() -> Option<Self> {
        let path = format!("{DIRECTORY}/audio.fifo");
        if !std::fs::metadata(&path).ok()?.file_type().is_fifo() {
            return None;
        }
        let file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NONBLOCK)
            .open(path)
            .ok()?;
        Some(Self::new(file))
    }
    fn push(&mut self, frame: [i16; 2]) {
        if self.len == CAPACITY {
            self.head = (self.head + 1) % CAPACITY;
            self.len -= 1;
        }
        self.samples[(self.head + self.len) % CAPACITY] = frame;
        self.len += 1;
    }
    fn ingest(&mut self, bytes: &[u8]) {
        for &byte in bytes {
            self.carry[self.carry_len] = byte;
            self.carry_len += 1;
            if self.carry_len == 4 {
                self.push([
                    i16::from_le_bytes([self.carry[0], self.carry[1]]),
                    i16::from_le_bytes([self.carry[2], self.carry[3]]),
                ]);
                self.carry_len = 0;
            }
        }
    }
    pub fn fill(&mut self, output: &mut [i16], enabled: bool) {
        output.fill(0);
        let mut bytes = [0u8; 4096];
        // Bounded nonblocking reads; discard incoming data throughout every alarm phase.
        for _ in 0..16 {
            match self.file.read(&mut bytes) {
                Ok(0) => {
                    self.carry_len = 0;
                    break;
                }
                Ok(count) => {
                    if enabled {
                        self.ingest(&bytes[..count]);
                    }
                }
                Err(error) if error.kind() == ErrorKind::Interrupted => continue,
                Err(error) if error.kind() == ErrorKind::WouldBlock => break,
                Err(_) => break,
            }
        }
        if !enabled {
            self.len = 0;
            self.head = 0;
            self.fraction = 0.0;
            self.playing = false;
            self.carry_len = 0;
            return;
        }
        if !self.playing && self.len >= PREBUFFER {
            self.playing = true;
        }
        if !self.playing {
            return;
        }
        // Absorb phone/USB clock drift; stereo channels share the same bounded timing correction.
        let step = (1.0 + (self.len as f64 - PREBUFFER as f64) / PREBUFFER as f64 * 0.0005)
            .clamp(0.998, 1.002);
        for frame in output.chunks_exact_mut(2) {
            if self.len < 2 {
                self.playing = false;
                self.fraction = 0.0;
                break;
            }
            for (channel, sample) in frame.iter_mut().enumerate() {
                let current = self.samples[self.head][channel] as f64;
                let next = self.samples[(self.head + 1) % CAPACITY][channel] as f64;
                *sample = (current + (next - current) * self.fraction).round() as i16;
            }
            self.fraction += step;
            while self.fraction >= 1.0 {
                self.head = (self.head + 1) % CAPACITY;
                self.len -= 1;
                self.fraction -= 1.0;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn fragmented_pcm_preserves_stereo_and_disabling_discards_queued_audio() -> std::io::Result<()>
    {
        let mut input = Input::new(File::open("/dev/null")?);
        for _ in 0..PREBUFFER {
            input.ingest(&[0xe8]);
            input.ingest(&[0x03, 0x18]);
            input.ingest(&[0xfc]); // left +1000, right -1000 across fragmented reads.
        }
        let mut output = [0; 512];
        input.fill(&mut output, true);
        assert!(output.chunks_exact(2).all(|frame| frame == [1000, -1000]));
        input.fill(&mut output, false);
        assert!(output.iter().all(|&sample| sample == 0));
        input.fill(&mut output, true);
        assert!(output.iter().all(|&sample| sample == 0));
        Ok(())
    }
    #[test]
    fn bluetooth_queue_is_bounded_even_if_input_runs_faster_than_output() -> std::io::Result<()> {
        let mut input = Input::new(File::open("/dev/null")?);
        for index in 0..CAPACITY * 3 {
            input.push([index as i16, -(index as i16)]);
        }
        assert_eq!(input.len, CAPACITY);
        let mut output = [0; 512];
        input.fill(&mut output, true);
        assert_eq!(output[0], (CAPACITY * 2) as i16);
        assert_eq!(output[1], -((CAPACITY * 2) as i16));
        Ok(())
    }
}
