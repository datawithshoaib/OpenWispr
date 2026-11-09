# OpenWispr

Fast, private, push-to-talk dictation for Windows, written in Rust.

Hold **Ctrl + Win**, speak, and release. Your words are typed into whatever app has
focus. Speech recognition runs entirely on your machine with a free
[Whisper](https://github.com/ggml-org/whisper.cpp) model, so nothing is sent to the cloud.

- **Fast:** about 0.25 s to transcribe a typical 3-second sentence on a 4-core laptop CPU
- **Light:** one 3.7 MB executable; the model loads in about 0.3 s; the mic is open only while you dictate
- **Simple:** no account, no settings UI, no background services

## Use it

```bash
openwispr
```

On first run it downloads the default model (`base.en`, 142 MB) plus a tiny voice-detection
model (under 1 MB) into `%LOCALAPPDATA%\OpenWispr\models`. After that it works offline.

| Action | What happens |
|---|---|
| Hold **Ctrl+Win**, speak, release | Text is inserted at the cursor |
| Tap **Ctrl+Win** quickly | Hands-free mode: keep talking, tap again to finish |
| **Esc** while hands-free | Cancel, nothing is inserted |
| Ctrl+Win + another key (e.g. Ctrl+Win+→) | Treated as a normal shortcut; dictation is cancelled |

You hear a short rising beep when recording starts and a falling one when it stops.

### Options

```bash
openwispr --model small.en          # more accurate, slower
openwispr --model tiny.en           # fastest
openwispr run --hotkey right-alt    # or f9, ctrl+shift+space, ...
openwispr run --type                # type keystrokes instead of pasting (clipboard untouched)
openwispr run --device "USB"        # pick a microphone by name
openwispr run --no-sound            # no beeps
openwispr --model base --language de   # multilingual model, German
```

Other commands:

```bash
openwispr models               # list free models (* = downloaded)
openwispr download -m small.en # fetch a model ahead of time
openwispr devices              # list microphones
openwispr mic-test             # record 5 s and print what was heard
openwispr transcribe file.wav  # transcribe a WAV file
```

By default text is inserted by pasting (fast and reliable for long text). Your previous
clipboard text is restored afterwards, and the dictated text is kept out of Windows
clipboard history.

## Build from source

Requirements: Rust (`rustup`), Visual Studio 2022+ (or Build Tools) with the C++ workload,
CMake, and LLVM/Clang (needed to generate whisper.cpp's Rust bindings).

```bash
cargo build --release
```

The binary is `target\release\openwispr.exe`. If CMake isn't on your `PATH`, use the copy
that ships with Visual Studio, for example
`C:\Program Files\Microsoft Visual Studio\18\Community\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin`.

`.cargo/config.toml` sets `CL=/O2`. Without it, whisper.cpp is compiled **unoptimized**
on MSVC: the `cmake` crate strips optimization flags under the Visual Studio generator, which makes
transcription about 10x slower. whisper.cpp is also built for the CPU you compile on
(e.g. AVX2/AVX-512), so build on the machine you'll run it on.

## Tests

```bash
cargo test --release
```

Unit tests cover the hotkey state machine (hold, tap/hands-free, cancel, Start-menu
masking), audio resampling, WAV loading, text cleanup, and model handling.

End-to-end test: this runs the real app and keyboard hook against a throwaway text box,
with a WAV file standing in for the microphone:

```bash
powershell -ExecutionPolicy Bypass -File tests\e2e.ps1 -Wav speech.wav -Expect "the words in it"
```

Add `-Type` to test typing mode. The script presses Ctrl+Win itself, so don't type while it runs.

## How it works

1. A low-level keyboard hook (`src/platform.rs`) feeds key events to a small state machine
   (`src/hotkey.rs`).
2. While recording, `cpal` captures the default microphone; audio is mixed to mono and resampled
   to 16 kHz (`src/audio.rs`).
3. On release, the Silero voice-activity detector checks the clip contains speech. Whisper then
   transcribes it with greedy decoding, and the encoder window is shrunk to fit the clip
   (`src/transcribe.rs`). That last step is what makes short dictations 5–8x faster than stock Whisper.
4. The text is pasted (or typed) into the focused window.

Live dictation is Windows-only for now. `transcribe` and `mic-test` are portable.
