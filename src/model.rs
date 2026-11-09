//! Locating and downloading free Whisper models (ggml format, from the whisper.cpp
//! Hugging Face repository).

use anyhow::{Context, Result, bail};
use std::fs::{self, File};
use std::io::{BufWriter, Read, Write};
use std::path::{Path, PathBuf};
use std::time::Instant;

const BASE_URL: &str = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main";

/// Silero voice-activity detector (under 1 MB), used to ignore clips without speech.
const VAD_URL: &str = "https://huggingface.co/ggml-org/whisper-vad/resolve/main/ggml-silero-v5.1.2.bin";
const VAD_FILE: &str = "ggml-silero-v5.1.2.bin";

/// The model used when none is specified: good accuracy, fast on any modern CPU.
pub const DEFAULT_MODEL: &str = "base.en";

/// Models known to exist in the whisper.cpp repository, with approximate download size.
pub const KNOWN_MODELS: &[(&str, &str)] = &[
    ("tiny.en", "75 MB   fastest, lowest accuracy (English)"),
    ("base.en", "142 MB  fast, good accuracy (English) [default]"),
    ("base.en-q5_1", "57 MB   quantized base.en, slightly less accurate"),
    ("small.en", "466 MB  slower, better accuracy (English)"),
    ("small.en-q5_1", "181 MB  quantized small.en"),
    ("tiny", "75 MB   multilingual"),
    ("base", "142 MB  multilingual"),
    ("small", "466 MB  multilingual"),
    ("large-v3-turbo-q5_0", "547 MB  multilingual, best accuracy, needs a fast CPU"),
];

/// ggml model files start with the magic number 0x67676d6c ("ggml"), stored little-endian.
const GGML_MAGIC: [u8; 4] = *b"lmgg";

pub fn models_dir() -> PathBuf {
    dirs::data_local_dir()
        .unwrap_or_else(|| PathBuf::from("."))
        .join("OpenWispr")
        .join("models")
}

pub fn file_name(model: &str) -> String {
    format!("ggml-{model}.bin")
}

pub fn url_for(model: &str) -> String {
    format!("{BASE_URL}/{}", file_name(model))
}

/// English-only models end in `.en` (optionally followed by a quantization suffix).
pub fn is_english_only(model: &str) -> bool {
    model.split('-').next().unwrap_or(model).ends_with(".en")
}

/// Rejects names that could escape the models directory or build a bogus URL.
pub fn validate_name(model: &str) -> Result<()> {
    let ok = !model.is_empty()
        && model
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_'))
        && !model.contains("..");
    if !ok {
        bail!("invalid model name '{model}' (try one of: openwispr models)");
    }
    Ok(())
}

/// Returns `true` if the file looks like a complete ggml model.
pub fn looks_valid(path: &Path) -> bool {
    let mut magic = [0u8; 4];
    File::open(path)
        .and_then(|mut f| f.read_exact(&mut magic))
        .map(|_| magic == GGML_MAGIC)
        .unwrap_or(false)
        && fs::metadata(path).map(|m| m.len() > 100_000).unwrap_or(false)
}

/// Resolves `model` to a local file, downloading it first if needed.
/// `model` may be a catalog name (e.g. `base.en`) or a path to a `.bin` file.
pub fn ensure(model: &str) -> Result<PathBuf> {
    let as_path = Path::new(model);
    if as_path.extension().is_some_and(|e| e == "bin") {
        if !looks_valid(as_path) {
            bail!("{} is not a valid ggml Whisper model", as_path.display());
        }
        return Ok(as_path.to_path_buf());
    }
    validate_name(model)?;
    let path = models_dir().join(file_name(model));
    if looks_valid(&path) {
        return Ok(path);
    }
    download(&url_for(model), &path, &format!("Whisper model '{model}'"))
        .with_context(|| format!("could not get model '{model}' (is the name right? see `openwispr models`)"))?;
    Ok(path)
}

/// Resolves the voice-activity-detection model, downloading it if needed.
pub fn ensure_vad() -> Result<PathBuf> {
    let path = models_dir().join(VAD_FILE);
    if !looks_valid(&path) {
        download(VAD_URL, &path, "voice detection model")?;
    }
    Ok(path)
}

/// Downloads `url` to `dest`, writing to a `.part` file first so an interrupted
/// download never leaves a truncated model behind.
fn download(url: &str, dest: &Path, label: &str) -> Result<()> {
    fs::create_dir_all(dest.parent().context("model path has no parent")?)?;
    eprintln!("Downloading {label}\n  from {url}\n  to   {}", dest.display());

    let mut resp = ureq::get(url).call().context("download failed")?;
    let total = resp.body().content_length();
    let mut reader = resp.body_mut().with_config().limit(u64::MAX).reader();

    let part = dest.with_extension("bin.part");
    let mut out = BufWriter::with_capacity(1 << 20, File::create(&part)?);
    let mut buf = vec![0u8; 1 << 16];
    let (mut done, start, mut last_pct) = (0u64, Instant::now(), u64::MAX);
    loop {
        let n = reader.read(&mut buf).context("network error while downloading")?;
        if n == 0 {
            break;
        }
        out.write_all(&buf[..n])?;
        done += n as u64;
        if let Some(total) = total {
            let pct = done * 100 / total.max(1);
            if pct != last_pct {
                last_pct = pct;
                let mbps = done as f64 / 1e6 / start.elapsed().as_secs_f64().max(0.001);
                eprint!("\r  {pct:3}%  {:.0}/{:.0} MB  {mbps:.1} MB/s ", done as f64 / 1e6, total as f64 / 1e6);
            }
        }
    }
    out.flush()?;
    drop(out);
    eprintln!();

    if let Some(total) = total
        && done != total
    {
        let _ = fs::remove_file(&part);
        bail!("download incomplete: got {done} of {total} bytes");
    }
    if !looks_valid(&part) {
        let _ = fs::remove_file(&part);
        bail!("downloaded file is not a ggml model");
    }
    fs::rename(&part, dest)?;
    eprintln!("  done in {:.1}s", start.elapsed().as_secs_f64());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn names_and_urls() {
        assert_eq!(file_name("base.en"), "ggml-base.en.bin");
        assert_eq!(
            url_for("tiny.en"),
            "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny.en.bin"
        );
    }

    #[test]
    fn english_only_detection() {
        assert!(is_english_only("base.en"));
        assert!(is_english_only("small.en-q5_1"));
        assert!(!is_english_only("base"));
        assert!(!is_english_only("large-v3-turbo-q5_0"));
    }

    #[test]
    fn rejects_unsafe_names() {
        assert!(validate_name("base.en").is_ok());
        assert!(validate_name("large-v3-turbo-q5_0").is_ok());
        assert!(validate_name("../evil").is_err());
        assert!(validate_name("a/b").is_err());
        assert!(validate_name("").is_err());
    }

    #[test]
    fn validity_check() {
        let dir = std::env::temp_dir().join(format!("openwispr-test-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let bad = dir.join("bad.bin");
        fs::write(&bad, b"not a model").unwrap();
        assert!(!looks_valid(&bad));
        let good = dir.join("good.bin");
        let mut data = GGML_MAGIC.to_vec();
        data.resize(200_000, 0);
        fs::write(&good, data).unwrap();
        assert!(looks_valid(&good));
        assert!(!looks_valid(&dir.join("missing.bin")));
        fs::remove_dir_all(&dir).unwrap();
    }
}
