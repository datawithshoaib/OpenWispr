# OpenWispr

Fast, private, push-to-talk dictation for Windows, written in Python.

Hold **Ctrl + Win**, speak, and release. Your words are typed into whatever app has
focus. Speech recognition runs entirely on your machine with a free
[Whisper](https://github.com/ggml-org/whisper.cpp) model, so nothing is sent to the cloud.

- **Fast:** about 0.25 s to transcribe a typical 3-second sentence on a 4-core laptop CPU
- **Light:** minimal memory footprint; the model loads in about 0.3 s; the mic is open only while you dictate
- **Simple:** no account, no settings UI, no background services

## Installation & Setup

Using [uv](https://github.com/astral-sh/uv):

```bash
# Create and activate virtual environment
uv venv
.venv\Scripts\activate

# Install dependencies and openwispr in editable mode
uv pip install -e ".[dev]"
```

## Use it

```bash
openwispr
# or: uv run openwispr
```

On first run it downloads the default model (`small.en`, 466 MB) plus a tiny voice-detection
model (under 1 MB) into `%LOCALAPPDATA%\OpenWispr\models`. After that it works offline.

| Action | What happens |
|---|---|
| Hold **Ctrl+Win**, speak, release | Text is inserted at the cursor |
| Tap **Ctrl+Win** quickly | Hands-free mode: keep talking, tap again to finish |
| **Esc** while hands-free | Cancel, nothing is inserted |
| Ctrl+Win + another key (e.g. Ctrl+Win+→) | Treated as a normal shortcut; dictation is cancelled |
| Click Quit or close the window | Finishes any Dictation, then ends the app |

You hear a short rising beep when recording starts and a falling one when it stops.

### Start without a terminal

```bash
openwispr install-desktop-shortcut      # double-click to open the app window
openwispr uninstall-desktop-shortcut
```

### Start at login

```bash
openwispr install-autostart     # add a Startup-folder shortcut (no console)
openwispr uninstall-autostart   # remove it
```

Quit from the app window ends the running app but leaves the shortcuts installed.

### Options

```bash
openwispr --model small.en-q5_1     # smaller and faster, less accurate
openwispr --model tiny.en           # fastest
openwispr --model large-v3-turbo-q5_0  # best accuracy (multilingual, needs a fast CPU)
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

## Tests

```bash
uv run pytest -v
```

Unit tests cover the chord state machine (hold, tap/hands-free, cancel, Start-menu
masking), audio resampling, WAV loading, text cleanup, and model handling.

End-to-end test: this runs the real app and keyboard hook against a throwaway text box,
with a WAV file standing in for the microphone:

```bash
powershell -ExecutionPolicy Bypass -File tests\e2e.ps1 -Wav speech.wav -Expect "the words in it"
```

Add `-Type` to test typing mode. The script presses Ctrl+Win itself, so don't type while it runs.

## How it works

1. A low-level keyboard hook (`openwispr/platform_win.py`) feeds key events to a small state machine
   (`openwispr/hotkey.py`).
2. While recording, `sounddevice` captures the default microphone; audio is mixed to mono and resampled
   to 16 kHz (`openwispr/audio.py`).
3. On release, the Silero voice-activity detector checks the clip contains speech. Whisper then
   transcribes it with greedy decoding, and the encoder window is shrunk to fit the clip
   (`openwispr/transcribe.py`). That last step is what makes short dictations 5–8x faster than stock Whisper.
4. The text is pasted (or typed) into the focused window.

Live dictation is Windows-only for now. `transcribe` and `mic-test` are portable.
