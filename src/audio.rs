//! Microphone capture and conversion to the 16 kHz mono f32 audio Whisper expects.

use anyhow::{Context, Result, anyhow, bail};
use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{ErrorKind, FromSample, SampleFormat};
use std::path::Path;
use std::sync::{Arc, Mutex};

pub const WHISPER_RATE: u32 = 16_000;

/// Records from an input device while active. The stream is opened on `start` and
/// closed on `stop`, so the microphone is only in use while dictating.
pub struct Recorder {
    device_name: Option<String>,
    active: Option<(cpal::Stream, Arc<Mutex<Vec<f32>>>, u32)>,
}

impl Recorder {
    pub fn new(device_name: Option<String>) -> Self {
        Self { device_name, active: None }
    }

    pub fn is_recording(&self) -> bool {
        self.active.is_some()
    }

    pub fn start(&mut self) -> Result<()> {
        if self.active.is_some() {
            return Ok(());
        }
        let device = find_input_device(self.device_name.as_deref())?;
        let config = device.default_input_config().context("no usable input config")?;
        let channels = config.channels() as usize;
        let rate = config.sample_rate();
        let buf = Arc::new(Mutex::new(Vec::with_capacity(rate as usize * 30)));

        let stream = match config.sample_format() {
            SampleFormat::F32 => build::<f32>(&device, &config.into(), channels, buf.clone())?,
            SampleFormat::I16 => build::<i16>(&device, &config.into(), channels, buf.clone())?,
            SampleFormat::I32 => build::<i32>(&device, &config.into(), channels, buf.clone())?,
            SampleFormat::U16 => build::<u16>(&device, &config.into(), channels, buf.clone())?,
            SampleFormat::I8 => build::<i8>(&device, &config.into(), channels, buf.clone())?,
            other => bail!("unsupported microphone sample format {other}"),
        };
        stream.play().context("could not start microphone")?;
        self.active = Some((stream, buf, rate));
        Ok(())
    }

    /// Stops recording and returns the captured audio as 16 kHz mono.
    pub fn stop(&mut self) -> Vec<f32> {
        let Some((stream, buf, rate)) = self.active.take() else {
            return Vec::new();
        };
        drop(stream);
        let samples = std::mem::take(&mut *buf.lock().unwrap());
        resample(&samples, rate, WHISPER_RATE)
    }
}

fn build<T>(
    device: &cpal::Device,
    config: &cpal::StreamConfig,
    channels: usize,
    buf: Arc<Mutex<Vec<f32>>>,
) -> Result<cpal::Stream>
where
    T: cpal::SizedSample,
    f32: FromSample<T>,
{
    let stream = device.build_input_stream(
        *config,
        move |data: &[T], _: &_| {
            if let Ok(mut out) = buf.lock() {
                out.extend(data.chunks(channels).map(|frame| {
                    frame.iter().map(|s| s.to_sample::<f32>()).sum::<f32>() / channels as f32
                }));
            }
        },
        |err| {
            // WASAPI reports an overrun when the stream opens; it is harmless.
            if err.kind() != ErrorKind::Xrun {
                eprintln!("microphone error: {err}");
            }
        },
        None,
    )?;
    Ok(stream)
}

fn find_input_device(name: Option<&str>) -> Result<cpal::Device> {
    let host = cpal::default_host();
    match name {
        None => host
            .default_input_device()
            .ok_or_else(|| anyhow!("no microphone found (check Windows sound settings)")),
        Some(wanted) => {
            let wanted = wanted.to_lowercase();
            host.input_devices()?
                .find(|d| device_name(d).to_lowercase().contains(&wanted))
                .ok_or_else(|| anyhow!("no microphone matching '{wanted}' (see `openwispr devices`)"))
        }
    }
}

pub fn device_name(d: &cpal::Device) -> String {
    d.description().map(|d| d.name().to_string()).unwrap_or_else(|_| "<unknown>".into())
}

/// Lists input device names, marking the default one.
pub fn list_devices() -> Result<Vec<(String, bool)>> {
    let host = cpal::default_host();
    let default = host.default_input_device().map(|d| device_name(&d));
    Ok(host
        .input_devices()?
        .map(|d| {
            let n = device_name(&d);
            let is_default = default.as_deref() == Some(n.as_str());
            (n, is_default)
        })
        .collect())
}

/// Converts mono audio between sample rates. Downsampling averages each output
/// sample's span of input (a cheap anti-alias filter that is plenty for speech);
/// upsampling interpolates linearly.
pub fn resample(input: &[f32], from: u32, to: u32) -> Vec<f32> {
    if from == to || input.is_empty() {
        return input.to_vec();
    }
    let ratio = from as f64 / to as f64;
    let out_len = (input.len() as f64 / ratio).floor() as usize;
    let mut out = Vec::with_capacity(out_len);
    if ratio > 1.0 {
        for i in 0..out_len {
            let start = (i as f64 * ratio) as usize;
            let end = (((i + 1) as f64 * ratio) as usize).clamp(start + 1, input.len());
            let span = &input[start..end];
            out.push(span.iter().sum::<f32>() / span.len() as f32);
        }
    } else {
        for i in 0..out_len {
            let pos = i as f64 * ratio;
            let idx = pos as usize;
            let frac = (pos - idx as f64) as f32;
            let a = input[idx];
            let b = *input.get(idx + 1).unwrap_or(&a);
            out.push(a + (b - a) * frac);
        }
    }
    out
}

pub fn rms(samples: &[f32]) -> f32 {
    if samples.is_empty() {
        return 0.0;
    }
    (samples.iter().map(|s| s * s).sum::<f32>() / samples.len() as f32).sqrt()
}

/// True when the clip is too short or too quiet to contain speech. Skipping these
/// avoids Whisper "hallucinating" text such as "Thank you." from silence.
pub fn is_silence(samples: &[f32]) -> bool {
    samples.len() < (WHISPER_RATE as usize) / 4 || rms(samples) < 0.0015
}

/// Loads any PCM WAV file as 16 kHz mono f32.
pub fn load_wav(path: &Path) -> Result<Vec<f32>> {
    let mut reader = hound::WavReader::open(path)
        .with_context(|| format!("cannot open {}", path.display()))?;
    let spec = reader.spec();
    let interleaved: Vec<f32> = match spec.sample_format {
        hound::SampleFormat::Float => reader.samples::<f32>().collect::<Result<_, _>>()?,
        hound::SampleFormat::Int => {
            let scale = 1.0 / (1i64 << (spec.bits_per_sample - 1)) as f32;
            reader
                .samples::<i32>()
                .map(|s| s.map(|v| v as f32 * scale))
                .collect::<Result<_, _>>()?
        }
    };
    let ch = spec.channels.max(1) as usize;
    let mono: Vec<f32> = interleaved
        .chunks(ch)
        .map(|f| f.iter().sum::<f32>() / ch as f32)
        .collect();
    Ok(resample(&mono, spec.sample_rate, WHISPER_RATE))
}

/// Saves 16 kHz mono audio as a 16-bit WAV file.
pub fn save_wav(path: &Path, samples: &[f32]) -> Result<()> {
    let spec = hound::WavSpec {
        channels: 1,
        sample_rate: WHISPER_RATE,
        bits_per_sample: 16,
        sample_format: hound::SampleFormat::Int,
    };
    let mut w = hound::WavWriter::create(path, spec)?;
    for &s in samples {
        w.write_sample((s.clamp(-1.0, 1.0) * i16::MAX as f32) as i16)?;
    }
    w.finalize()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sine(freq: f32, rate: u32, secs: f32) -> Vec<f32> {
        (0..(rate as f32 * secs) as usize)
            .map(|i| (i as f32 / rate as f32 * freq * std::f32::consts::TAU).sin() * 0.5)
            .collect()
    }

    #[test]
    fn resample_identity() {
        let x = vec![0.1, 0.2, 0.3];
        assert_eq!(resample(&x, 16_000, 16_000), x);
    }

    #[test]
    fn resample_lengths() {
        assert_eq!(resample(&vec![0.0; 48_000], 48_000, 16_000).len(), 16_000);
        assert_eq!(resample(&vec![0.0; 44_100], 44_100, 16_000).len(), 16_000);
        assert_eq!(resample(&vec![0.0; 8_000], 8_000, 16_000).len(), 16_000);
    }

    #[test]
    fn resample_preserves_tone() {
        // A 440 Hz tone should keep its energy (RMS of a 0.5 sine is ~0.354).
        for from in [48_000, 44_100, 22_050, 8_000] {
            let out = resample(&sine(440.0, from, 1.0), from, 16_000);
            let r = rms(&out);
            assert!((r - 0.354).abs() < 0.02, "rate {from}: rms {r}");
        }
    }

    #[test]
    fn upsample_interpolates() {
        assert_eq!(resample(&[0.0, 1.0], 1, 2), vec![0.0, 0.5, 1.0, 1.0]);
    }

    #[test]
    fn silence_detection() {
        assert!(is_silence(&[]));
        assert!(is_silence(&vec![0.0; 16_000]));
        assert!(is_silence(&sine(200.0, 16_000, 0.1))); // too short
        assert!(!is_silence(&sine(200.0, 16_000, 1.0)));
    }

    #[test]
    fn wav_roundtrip_stereo_44k() {
        let path = std::env::temp_dir().join(format!("openwispr-{}.wav", std::process::id()));
        let spec = hound::WavSpec {
            channels: 2,
            sample_rate: 44_100,
            bits_per_sample: 16,
            sample_format: hound::SampleFormat::Int,
        };
        let mut w = hound::WavWriter::create(&path, spec).unwrap();
        for s in sine(300.0, 44_100, 0.5) {
            let v = (s * i16::MAX as f32) as i16;
            w.write_sample(v).unwrap();
            w.write_sample(v).unwrap();
        }
        w.finalize().unwrap();
        let out = load_wav(&path).unwrap();
        std::fs::remove_file(&path).unwrap();
        assert_eq!(out.len(), 8_000);
        assert!((rms(&out) - 0.354).abs() < 0.02);
    }
}
