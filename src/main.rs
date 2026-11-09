//! OpenWispr: hold a hotkey, speak, release — your words are typed into whatever
//! app has focus. Speech recognition runs locally with a free Whisper model.

mod audio;
mod hotkey;
mod model;
#[cfg(windows)]
mod platform;
mod transcribe;
#[cfg(windows)]
mod tray;

use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use std::path::PathBuf;
use std::time::Instant;
use transcribe::Transcriber;

#[derive(Parser)]
#[command(version, about = "Fast, local push-to-talk dictation powered by Whisper")]
struct Cli {
    #[command(subcommand)]
    command: Option<Command>,
    #[command(flatten)]
    opts: Opts,
}

#[derive(clap::Args, Clone)]
struct Opts {
    /// Model name (see `openwispr models`) or path to a ggml .bin file
    #[arg(short, long, global = true, default_value = model::DEFAULT_MODEL)]
    model: String,
    /// Spoken language code (e.g. en, de, fr) or "auto"; English-only models always use en
    #[arg(short, long, global = true)]
    language: Option<String>,
    /// CPU threads for transcription (default: number of physical cores, up to 8)
    #[arg(short, long, global = true)]
    threads: Option<i32>,
}

#[derive(Subcommand)]
enum Command {
    /// Start dictation (the default when no command is given)
    Run(RunArgs),
    /// Download a model without starting dictation
    Download,
    /// List downloadable models
    Models,
    /// List microphones
    Devices,
    /// Transcribe a WAV file and print the text
    Transcribe {
        wav: PathBuf,
        /// Benchmarking aid: transcribe this many times and report each timing
        #[arg(long, default_value_t = 1, hide = true)]
        repeat: u32,
    },
    /// Record from the microphone for a few seconds and print the text
    MicTest {
        #[arg(short, long, default_value_t = 5)]
        seconds: u64,
        #[arg(short, long)]
        device: Option<String>,
        /// Also save the recording (16 kHz mono) to this WAV file
        #[arg(long)]
        save: Option<PathBuf>,
    },
}

#[derive(clap::Args, Clone, Default)]
struct RunArgs {
    /// Push-to-talk hotkey, e.g. ctrl+win, right-alt, f9, ctrl+shift+space
    #[arg(short = 'k', long, default_value = "ctrl+win")]
    hotkey: String,
    /// Microphone name (substring match; see `openwispr devices`)
    #[arg(short, long)]
    device: Option<String>,
    /// Type the text as keystrokes instead of pasting it (keeps the clipboard untouched)
    #[arg(long)]
    r#type: bool,
    /// Disable the start/stop sounds
    #[arg(long)]
    no_sound: bool,
    /// Testing aid: use audio from this WAV file instead of the microphone
    #[arg(long, hide = true)]
    fake_mic: Option<PathBuf>,
}

fn main() {
    let cli = Cli::parse();
    if let Err(e) = run_cli(cli) {
        eprintln!("error: {e:#}");
        std::process::exit(1);
    }
}

fn run_cli(cli: Cli) -> Result<()> {
    let opts = cli.opts;
    match cli.command.unwrap_or_else(|| Command::Run(RunArgs { hotkey: "ctrl+win".into(), ..Default::default() })) {
        Command::Models => {
            println!("Free Whisper models (downloaded to {}):\n", model::models_dir().display());
            for (name, desc) in model::KNOWN_MODELS {
                let have = model::looks_valid(&model::models_dir().join(model::file_name(name)));
                println!("  {} {name:<22} {desc}", if have { "*" } else { " " });
            }
            println!("\n  * = downloaded.  Use with: openwispr --model <name>");
        }
        Command::Devices => {
            for (name, is_default) in audio::list_devices()? {
                println!("{} {name}", if is_default { "*" } else { " " });
            }
        }
        Command::Download => {
            let path = model::ensure(&opts.model)?;
            println!("Model ready: {}", path.display());
        }
        Command::Transcribe { wav, repeat } => {
            let samples = audio::load_wav(&wav)?;
            let mut t = load_transcriber(&opts)?;
            let mut text = String::new();
            for _ in 0..repeat.max(1) {
                let start = Instant::now();
                text = t.transcribe(&samples)?;
                eprintln!(
                    "[{:.1}s of audio transcribed in {:.2}s]",
                    samples.len() as f32 / audio::WHISPER_RATE as f32,
                    start.elapsed().as_secs_f32()
                );
            }
            println!("{text}");
        }
        Command::MicTest { seconds, device, save } => {
            let mut t = load_transcriber(&opts)?;
            let mut rec = audio::Recorder::new(device);
            rec.start()?;
            eprintln!("Recording for {seconds}s... speak now.");
            std::thread::sleep(std::time::Duration::from_secs(seconds));
            let samples = rec.stop();
            eprintln!("Captured {:.1}s, level {:.4} RMS", samples.len() as f32 / 16_000.0, audio::rms(&samples));
            if let Some(path) = save {
                audio::save_wav(&path, &samples)?;
                eprintln!("Saved {}", path.display());
            }
            let start = Instant::now();
            let text = if audio::is_silence(&samples) { String::new() } else { t.transcribe(&samples)? };
            eprintln!("[transcribed in {:.2}s]", start.elapsed().as_secs_f32());
            println!("{text}");
        }
        Command::Run(args) => dictate(&opts, args)?,
    }
    Ok(())
}

fn load_transcriber(opts: &Opts) -> Result<Transcriber> {
    let path = model::ensure(&opts.model)?;
    let language = if model::is_english_only(&opts.model) {
        Some("en".to_string())
    } else {
        opts.language.clone().filter(|l| l != "auto")
    };
    // whisper.cpp runs best with one thread per physical core; hyperthreads only
    // add contention, so assume two logical processors per core.
    let threads = opts.threads.unwrap_or_else(|| {
        let logical = std::thread::available_parallelism().map(|n| n.get()).unwrap_or(4);
        (if logical >= 4 { logical / 2 } else { logical }).min(8) as i32
    });
    // Voice detection keeps silence and background noise from turning into
    // made-up text; dictation still works without it.
    let vad = model::ensure_vad()
        .inspect_err(|e| eprintln!("warning: voice detection unavailable ({e:#}); continuing without it"))
        .ok();
    let start = Instant::now();
    let t = Transcriber::load(&path, vad.as_deref(), language, threads)?;
    eprintln!("Loaded {} in {:.2}s ({threads} threads)", path.display(), start.elapsed().as_secs_f32());
    Ok(t)
}

#[cfg(not(windows))]
fn dictate(_opts: &Opts, _args: RunArgs) -> Result<()> {
    anyhow::bail!("live dictation is currently implemented for Windows only; `transcribe` and `mic-test` work everywhere")
}

#[cfg(windows)]
fn dictate(opts: &Opts, args: RunArgs) -> Result<()> {
    use hotkey::Action;
    use platform::Cue;
    use std::sync::mpsc;

    let chord = hotkey::Chord::parse(&args.hotkey)?;
    let mut transcriber = load_transcriber(opts)?;
    // Warm up once so the first real dictation is as fast as the rest.
    let _ = transcriber.transcribe(&vec![0.0; 16_000]);

    // Start the system tray icon.
    let tray_tx = tray::spawn_tray();

    // Transcribe and insert on a worker thread so the hotkey stays responsive.
    let (work_tx, work_rx) = mpsc::channel::<Vec<f32>>();
    let use_typing = args.r#type;
    std::thread::spawn(move || {
        // Guard against duplicate output: if the same text was just transcribed
        // within the last few seconds, skip it. Whisper sometimes produces
        // identical output from overlapping or repeated audio.
        let mut last_text = String::new();
        let mut last_time = std::time::Instant::now();
        const DEDUP_WINDOW: std::time::Duration = std::time::Duration::from_secs(5);

        for samples in work_rx {
            let secs = samples.len() as f32 / audio::WHISPER_RATE as f32;
            let start = std::time::Instant::now();
            match transcriber.transcribe(&samples) {
                Ok(text) if text.is_empty() => eprintln!("  (no speech detected)"),
                Ok(text) => {
                    eprintln!("  {secs:.1}s -> {:.2}s: {text}", start.elapsed().as_secs_f32());

                    // Skip exact duplicate within the dedup window.
                    let now = std::time::Instant::now();
                    if text == last_text && now.duration_since(last_time) < DEDUP_WINDOW {
                        eprintln!("  (duplicate suppressed)");
                        continue;
                    }
                    last_text = text.clone();
                    last_time = now;

                    // Separate consecutive dictations with a space.
                    let text = format!("{text} ");
                    let result = if use_typing { platform::type_text(&text) } else { platform::paste_text(&text) };
                    if let Err(e) = result {
                        eprintln!("  could not insert text: {e:#}");
                    }
                }
                Err(e) => eprintln!("  {e:#}"),
            }
        }
    });

    let (tx, rx) = mpsc::channel();
    platform::spawn_hotkey_listener(chord.clone(), tx)?;
    let mut source = AudioSource::new(&args)?;
    let sound = !args.no_sound;
    let tray = tray_tx.clone();
    let cue = |c| {
        if sound {
            platform::play_cue(c)
        }
    };

    eprintln!(
        "\nReady. Hold {} and speak; release to insert the text.\n\
         Tap it to dictate hands-free, tap again to stop. Esc cancels. Ctrl+C quits.\n",
        chord.label()
    );

    for action in rx {
        match action {
            Action::Start => match source.start() {
                Ok(()) => {
                    cue(Cue::Start);
                    let _ = tray.send(tray::TrayCommand::StartListening);
                    eprintln!("● listening...");
                }
                Err(e) => eprintln!("  microphone error: {e:#}"),
            },
            Action::Stop if source.is_recording() => {
                cue(Cue::Stop);
                let _ = tray.send(tray::TrayCommand::StopListening);
                let samples = source.stop();
                if audio::is_silence(&samples) {
                    eprintln!("  (too short or silent, ignored)");
                } else {
                    work_tx.send(samples).context("transcription worker stopped")?;
                }
            }
            Action::Cancel if source.is_recording() => {
                source.stop();
                let _ = tray.send(tray::TrayCommand::StopListening);
                cue(Cue::Cancel);
                eprintln!("  cancelled");
            }
            _ => {}
        }
    }
    let _ = tray_tx.send(tray::TrayCommand::Quit);
    Ok(())
}

/// The microphone, or a WAV file standing in for it in automated tests.
enum AudioSource {
    Mic(audio::Recorder),
    File { samples: Vec<f32>, recording: bool },
}

impl AudioSource {
    fn new(args: &RunArgs) -> Result<Self> {
        Ok(match &args.fake_mic {
            Some(path) => Self::File { samples: audio::load_wav(path)?, recording: false },
            None => Self::Mic(audio::Recorder::new(args.device.clone())),
        })
    }

    fn start(&mut self) -> Result<()> {
        match self {
            Self::Mic(r) => r.start(),
            Self::File { recording, .. } => {
                *recording = true;
                Ok(())
            }
        }
    }

    fn is_recording(&self) -> bool {
        match self {
            Self::Mic(r) => r.is_recording(),
            Self::File { recording, .. } => *recording,
        }
    }

    fn stop(&mut self) -> Vec<f32> {
        match self {
            Self::Mic(r) => r.stop(),
            Self::File { samples, recording } => {
                *recording = false;
                samples.clone()
            }
        }
    }
}
