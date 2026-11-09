//! Speech-to-text with a local Whisper model (whisper.cpp via whisper-rs).

use anyhow::{Context, Result};
use std::path::Path;
use whisper_rs::{
    FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters, WhisperState, WhisperVadContext,
    WhisperVadContextParams, WhisperVadParams,
};

pub struct Transcriber {
    // The state holds the model's working buffers (and keeps the model alive);
    // reusing it avoids re-allocating them on every dictation.
    state: WhisperState,
    vad: Option<WhisperVadContext>,
    language: Option<String>,
    threads: i32,
}

impl Transcriber {
    pub fn load(model: &Path, vad_model: Option<&Path>, language: Option<String>, threads: i32) -> Result<Self> {
        whisper_rs::install_logging_hooks(); // silence whisper.cpp's console chatter
        let ctx = WhisperContext::new_with_params(
            model.to_str().context("model path is not valid UTF-8")?,
            WhisperContextParameters::default(),
        )
        .context("failed to load Whisper model")?;
        let state = ctx.create_state().context("failed to create Whisper state")?;
        let vad = match vad_model {
            Some(path) => {
                let mut p = WhisperVadContextParams::default();
                p.set_n_threads(1);
                p.set_use_gpu(false);
                let path = path.to_str().context("VAD model path is not valid UTF-8")?;
                Some(WhisperVadContext::new(path, p).context("failed to load voice detection model")?)
            }
            None => None,
        };
        Ok(Self { state, vad, language, threads })
    }

    /// Whether the clip contains any speech. Without a VAD model, assume it does.
    fn has_speech(&mut self, audio: &[f32]) -> Result<bool> {
        let Some(vad) = self.vad.as_mut() else {
            return Ok(true);
        };
        let segments = vad
            .segments_from_samples(WhisperVadParams::new(), audio)
            .context("voice detection failed")?;
        Ok(segments.into_iter().next().is_some())
    }

    /// Transcribes 16 kHz mono audio into cleaned-up text. Returns an empty string
    /// when the clip contains no speech.
    pub fn transcribe(&mut self, audio: &[f32]) -> Result<String> {
        if !self.has_speech(audio)? {
            return Ok(String::new());
        }

        let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
        params.set_n_threads(self.threads);
        params.set_language(Some(self.language.as_deref().unwrap_or("auto")));
        params.set_translate(false);
        params.set_no_context(true);
        params.set_no_timestamps(true);
        params.set_suppress_blank(true);
        params.set_suppress_nst(true);
        params.set_print_special(false);
        params.set_print_progress(false);
        params.set_print_realtime(false);
        params.set_print_timestamps(false);
        // No re-decoding at higher temperatures: retries rarely help short
        // dictations and can take seconds when the model gets stuck.
        params.set_temperature_inc(0.0);

        // whisper.cpp needs at least ~1 s of audio; pad short clips with silence.
        let mut padded;
        let audio = if audio.len() < 16_000 + 1_600 {
            padded = audio.to_vec();
            padded.resize(16_000 + 1_600, 0.0);
            &padded[..]
        } else {
            audio
        };

        if let Some(frames) = audio_ctx_for(audio.len()) {
            params.set_audio_ctx(frames);
            // Stop runaway repetition ("music music music...") early.
            params.set_max_tokens(max_tokens_for(audio.len()));
        }
        self.state.full(params, audio).context("transcription failed")?;
        let mut text = String::new();
        for segment in self.state.as_iter() {
            if let Ok(s) = segment.to_str_lossy() {
                text.push_str(&s);
            }
        }
        Ok(clean_text(&text))
    }
}

/// An upper bound on tokens a clip can produce: even very fast speech is under
/// ~5 tokens per second, so this leaves generous headroom.
pub fn max_tokens_for(samples: usize) -> i32 {
    (samples as f64 / 16_000.0 * 8.0).ceil() as i32 + 16
}

/// Whisper's encoder always processes a 30 s window (1500 frames), even for a
/// 3 s clip. Shrinking the window to fit the audio (plus a small margin) makes
/// short dictations 5-8x faster with the same transcript.
pub fn audio_ctx_for(samples: usize) -> Option<i32> {
    const FULL: i32 = 1500;
    const MARGIN: i32 = 64;
    let frames = (samples as f64 / 16_000.0 / 30.0 * FULL as f64).ceil() as i32 + MARGIN;
    (frames < FULL).then_some(frames)
}

/// Removes Whisper's non-speech annotations (e.g. `[BLANK_AUDIO]`, `(music)`,
/// `*laughs*`) and normalizes whitespace.
pub fn clean_text(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    let mut closing: Option<char> = None;
    for c in raw.chars() {
        match closing {
            Some(end) if c == end => closing = None,
            Some(_) => {}
            None => match c {
                '[' => closing = Some(']'),
                '(' => closing = Some(')'),
                '*' => closing = Some('*'),
                _ => out.push(c),
            },
        }
    }
    // Collapse runaway repetition ("Music Music Music Music ...") to one word;
    // natural speech rarely repeats a word more than three times in a row.
    let words: Vec<&str> = out.split_whitespace().collect();
    let mut kept: Vec<&str> = Vec::with_capacity(words.len());
    let mut i = 0;
    while i < words.len() {
        let run = words[i..].iter().take_while(|w| w.eq_ignore_ascii_case(words[i])).count();
        kept.extend(std::iter::repeat_n(words[i], if run > 3 { 1 } else { run }));
        i += run;
    }
    let text = kept.join(" ");
    // Drop leftovers that are only punctuation, e.g. "." from a bracketed clip.
    if text.chars().all(|c| !c.is_alphanumeric()) {
        return String::new();
    }
    text
}

#[cfg(test)]
mod tests {
    use super::{audio_ctx_for, clean_text, max_tokens_for};

    #[test]
    fn token_cap_scales_with_length() {
        assert_eq!(max_tokens_for(16_000 * 2), 32);
        assert_eq!(max_tokens_for(16_000 * 10), 96);
    }

    #[test]
    fn audio_ctx_scales_with_length() {
        assert_eq!(audio_ctx_for(16_000 * 3), Some(150 + 64));
        assert_eq!(audio_ctx_for(16_000 * 10), Some(500 + 64));
        assert_eq!(audio_ctx_for(16_000 * 29), None); // close to the full window
        assert_eq!(audio_ctx_for(16_000 * 120), None);
    }

    #[test]
    fn strips_annotations() {
        assert_eq!(clean_text(" [BLANK_AUDIO]"), "");
        assert_eq!(clean_text("(music) Hello there."), "Hello there.");
        assert_eq!(clean_text(" Hello *laughs* world "), "Hello world");
        assert_eq!(clean_text("[Music] ."), "");
    }

    #[test]
    fn normalizes_whitespace() {
        assert_eq!(clean_text("  Hello,\n  how are   you? "), "Hello, how are you?");
    }

    #[test]
    fn collapses_runaway_repetition() {
        assert_eq!(clean_text("Music Music Music Music Music"), "Music");
        assert_eq!(clean_text("no no no, it is very very good"), "no no no, it is very very good");
    }

    #[test]
    fn keeps_normal_text() {
        assert_eq!(clean_text(" It's 5 o'clock."), "It's 5 o'clock.");
        assert_eq!(clean_text("Caf\u{e9} na\u{ef}ve"), "Caf\u{e9} na\u{ef}ve");
    }
}
