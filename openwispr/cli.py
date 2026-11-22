"""
OpenWispr CLI: Push-to-talk dictation for Windows powered by Whisper.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from queue import Empty, Queue
import threading
import numpy as np

from openwispr import __version__, audio, autostart, model, transcribe
from openwispr.hotkey import Action, Chord

if sys.platform == "win32":
    from openwispr import platform_win, tray


def default_threads() -> int:
    logical = os.cpu_count() or 4
    cores = logical // 2 if logical >= 4 else logical
    return max(1, min(8, cores))


def ensure_output_streams() -> None:
    if sys.stdout is not None and sys.stderr is not None:
        return
    local_app_data = os.environ.get("LOCALAPPDATA")
    log_root = Path(local_app_data) if local_app_data else (Path.home() / ".local" / "share")
    log_dir = log_root / "OpenWispr"
    log_dir.mkdir(parents=True, exist_ok=True)
    log = (log_dir / "openwispr.log").open("a", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = log
    if sys.stderr is None:
        sys.stderr = log


def load_transcriber(model_name: str, language: str | None, threads: int | None) -> transcribe.Transcriber:
    path = model.ensure(model_name)
    lang = "en" if model.is_english_only(model_name) else (language if language != "auto" else None)
    n_threads = threads if threads is not None else default_threads()

    vad_path: Path | None = None
    try:
        vad_path = model.ensure_vad()
    except Exception as e:
        sys.stderr.write(f"warning: voice detection unavailable ({e}); continuing without it\n")
        sys.stderr.flush()

    start = time.time()
    t = transcribe.Transcriber.load(path, vad_path, lang, n_threads)
    elapsed = time.time() - start
    sys.stderr.write(f"Loaded {path} in {elapsed:.2f}s ({n_threads} threads)\n")
    sys.stderr.flush()
    return t


class FakeMicSource:
    def __init__(self, path: Path) -> None:
        self.samples = audio.load_wav(path)
        self.recording = False

    def start(self) -> None:
        self.recording = True

    def is_recording(self) -> bool:
        return self.recording

    def stop(self) -> np.ndarray:
        self.recording = False
        return self.samples.copy()


class AudioSource:
    def __init__(self, fake_mic: str | Path | None, device: str | None) -> None:
        if fake_mic:
            self.inner = FakeMicSource(Path(fake_mic))
        else:
            self.inner = audio.Recorder(device)

    def start(self) -> None:
        self.inner.start()

    def is_recording(self) -> bool:
        return self.inner.is_recording()

    def stop(self) -> np.ndarray:
        return self.inner.stop()


def dictate(
    model_name: str,
    language: str | None,
    threads: int | None,
    hotkey_spec: str,
    device: str | None,
    use_typing: bool,
    no_sound: bool,
    fake_mic: str | None,
) -> None:
    if sys.platform != "win32":
        raise RuntimeError("live dictation is currently implemented for Windows only; `transcribe` and `mic-test` work everywhere")

    chord = Chord.parse(hotkey_spec)
    transcriber = load_transcriber(model_name, language, threads)

    # Warm up once so the first real dictation is as fast as the rest.
    transcriber.transcribe(np.zeros(16_000, dtype=np.float32))

    quit_queue: Queue[tray.TrayCommand] = Queue()
    tray_handle = tray.spawn_tray(chord.label, quit_queue)
    work_queue: Queue[np.ndarray | None] = Queue()

    def worker():
        last_text = ""
        last_time = 0.0
        dedup_window = 5.0

        while True:
            samples = work_queue.get()
            if samples is None:
                break
            secs = len(samples) / audio.WHISPER_RATE
            start = time.time()
            try:
                text = transcriber.transcribe(samples)
                if not text:
                    sys.stderr.write("  (no speech detected)\n")
                    sys.stderr.flush()
                else:
                    duration = time.time() - start
                    sys.stderr.write(f"  {secs:.1f}s -> {duration:.2f}s: {text}\n")
                    sys.stderr.flush()

                    now = time.time()
                    if text == last_text and (now - last_time) < dedup_window:
                        sys.stderr.write("  (duplicate suppressed)\n")
                        sys.stderr.flush()
                        continue
                    last_text = text
                    last_time = now

                    # Separate consecutive dictations with a space
                    text_with_space = f"{text} "
                    try:
                        if use_typing:
                            platform_win.type_text(text_with_space)
                        else:
                            platform_win.paste_text(text_with_space)
                    except Exception as e:
                        sys.stderr.write(f"  could not insert text: {e}\n")
                        sys.stderr.flush()
            except Exception as e:
                sys.stderr.write(f"  {e}\n")
                sys.stderr.flush()

    worker_thread = threading.Thread(target=worker, daemon=True)
    worker_thread.start()

    action_queue: Queue[Action] = Queue()
    platform_win.spawn_hotkey_listener(chord, action_queue)

    source = AudioSource(fake_mic, device)
    sound = not no_sound

    def cue(c: platform_win.Cue) -> None:
        if sound:
            platform_win.play_cue(c)

    sys.stderr.write(
        f"\nReady. Hold {chord.label} and speak; release to insert the text.\n"
        "Tap it to dictate hands-free, tap again to stop. Esc cancels.\n"
        "Right-click the Tray and choose Quit. Ctrl+C also quits.\n\n"
    )
    sys.stderr.flush()

    try:
        while True:
            try:
                if quit_queue.get_nowait() == tray.TrayCommand.Quit:
                    break
            except Empty:
                pass

            # Poll with a timeout: on Windows a blocking Queue.get() can't be
            # interrupted, so Ctrl+C would never reach us.
            try:
                action = action_queue.get(timeout=0.2)
            except Empty:
                continue
            if action == Action.Start:
                try:
                    source.start()
                    cue(platform_win.Cue.Start)
                    tray_handle.send(tray.TrayCommand.StartListening)
                    sys.stderr.write("● listening...\n")
                    sys.stderr.flush()
                except Exception as e:
                    sys.stderr.write(f"  microphone error: {e}\n")
                    sys.stderr.flush()
            elif action == Action.Stop:
                if source.is_recording():
                    cue(platform_win.Cue.Stop)
                    tray_handle.send(tray.TrayCommand.StopListening)
                    samples = source.stop()
                    if audio.is_silence(samples):
                        sys.stderr.write("  (too short or silent, ignored)\n")
                        sys.stderr.flush()
                    else:
                        work_queue.put(samples)
            elif action == Action.Cancel:
                if source.is_recording():
                    source.stop()
                    tray_handle.send(tray.TrayCommand.StopListening)
                    cue(platform_win.Cue.Cancel)
                    sys.stderr.write("  cancelled\n")
                    sys.stderr.flush()
    except KeyboardInterrupt:
        pass
    finally:
        tray_handle.send(tray.TrayCommand.Quit)
        work_queue.put(None)


SUBCOMMANDS = (
    "run",
    "models",
    "devices",
    "download",
    "transcribe",
    "mic-test",
    "install-autostart",
    "uninstall-autostart",
    "install-desktop-shortcut",
    "uninstall-desktop-shortcut",
)


def _add_common_options(p: argparse.ArgumentParser, *, top_level: bool) -> None:
    """
    Model options are accepted both before and after the subcommand
    (`openwispr -m tiny.en run` and `openwispr download -m tiny.en`). Defaults
    live only on the top-level parser; subcommand copies use SUPPRESS so they
    don't overwrite a value given before the subcommand.
    """
    def default(value):
        return value if top_level else argparse.SUPPRESS

    p.add_argument(
        "-m", "--model",
        default=default(model.DEFAULT_MODEL),
        help="Model name (see `openwispr models`) or path to a ggml .bin file",
    )
    p.add_argument(
        "-l", "--language",
        default=default(None),
        help="Spoken language code (e.g. en, de, fr) or 'auto'; English-only models always use en",
    )
    p.add_argument(
        "-t", "--threads",
        type=int,
        default=default(None),
        help="CPU threads for transcription (default: number of physical cores, up to 8)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="openwispr",
        description="Fast, local push-to-talk dictation powered by Whisper",
    )
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {__version__}")
    _add_common_options(parser, top_level=True)

    common = argparse.ArgumentParser(add_help=False)
    _add_common_options(common, top_level=False)

    subparsers = parser.add_subparsers(dest="command")

    # run command
    run_parser = subparsers.add_parser(
        "run", parents=[common], help="Start dictation (the default when no command is given)"
    )
    run_parser.add_argument("-k", "--hotkey", default="ctrl+win", help="Push-to-talk hotkey, e.g. ctrl+win, right-alt, f9")
    run_parser.add_argument("-d", "--device", default=None, help="Microphone name (substring match; see `openwispr devices`)")
    run_parser.add_argument("--type", action="store_true", help="Type the text as keystrokes instead of pasting it")
    run_parser.add_argument("--no-sound", action="store_true", help="Disable the start/stop sounds")
    run_parser.add_argument("--fake-mic", default=None, help=argparse.SUPPRESS)

    # models command
    subparsers.add_parser("models", help="List downloadable models")

    # devices command
    subparsers.add_parser("devices", help="List microphones")

    # download command
    subparsers.add_parser("download", parents=[common], help="Download a model without starting dictation")

    # transcribe command
    trans_parser = subparsers.add_parser(
        "transcribe", parents=[common], help="Transcribe a WAV file and print the text"
    )
    trans_parser.add_argument("wav", type=Path, help="Path to WAV file")
    trans_parser.add_argument("--repeat", type=int, default=1, help=argparse.SUPPRESS)

    # mic-test command
    mic_parser = subparsers.add_parser(
        "mic-test", parents=[common], help="Record from the microphone for a few seconds and print the text"
    )
    mic_parser.add_argument("-s", "--seconds", type=int, default=5, help="Duration in seconds")
    mic_parser.add_argument("-d", "--device", default=None, help="Microphone name")
    mic_parser.add_argument("--save", type=Path, default=None, help="Save recording to WAV file")

    # launcher commands
    subparsers.add_parser("install-autostart", help="Launch OpenWispr at Windows login")
    subparsers.add_parser("uninstall-autostart", help="Remove Windows login autostart")
    subparsers.add_parser("install-desktop-shortcut", help="Add an on-demand OpenWispr Desktop shortcut")
    subparsers.add_parser("uninstall-desktop-shortcut", help="Remove the OpenWispr Desktop shortcut")

    return parser


def normalize_argv(argv: list[str]) -> list[str]:
    """
    With no subcommand, default to 'run'. Prepending it (rather than appending)
    lets run-only options work without naming it: `openwispr --hotkey f9`.
    """
    has_subcommand = any(arg in SUBCOMMANDS for arg in argv)
    if not has_subcommand and not any(arg in ("-h", "--help", "-V", "--version") for arg in argv):
        return ["run", *argv]
    return list(argv)


def main() -> None:
    ensure_output_streams()
    parser = build_parser()

    args = parser.parse_args(normalize_argv(sys.argv[1:]))

    cmd = args.command or "run"
    if cmd == "models":
        mdir = model.models_dir()
        print(f"Free Whisper models (downloaded to {mdir}):\n")
        for name, desc in model.KNOWN_MODELS:
            have = model.looks_valid(mdir / model.file_name(name))
            marker = "*" if have else " "
            print(f"  {marker} {name:<22} {desc}")
        print("\n  * = downloaded.  Use with: openwispr --model <name>")

    elif cmd == "devices":
        for name, is_default in audio.list_devices():
            marker = "*" if is_default else " "
            print(f"{marker} {name}")

    elif cmd == "download":
        p = model.ensure(args.model)
        print(f"Model ready: {p}")

    elif cmd in (
        "install-autostart",
        "uninstall-autostart",
        "install-desktop-shortcut",
        "uninstall-desktop-shortcut",
    ):
        if sys.platform != "win32":
            raise RuntimeError("launcher shortcuts are Windows-only")
        if cmd == "install-autostart":
            print(f"Autostart installed: {autostart.install()}")
        elif cmd == "uninstall-autostart":
            print("Autostart removed" if autostart.uninstall() else "Autostart was not installed")
        elif cmd == "install-desktop-shortcut":
            print(f"Desktop shortcut installed: {autostart.install_desktop()}")
        else:
            print(
                "Desktop shortcut removed"
                if autostart.uninstall_desktop()
                else "Desktop shortcut was not installed"
            )

    elif cmd == "transcribe":
        samples = audio.load_wav(args.wav)
        t = load_transcriber(args.model, args.language, args.threads)
        text = ""
        repeat = max(1, args.repeat)
        for _ in range(repeat):
            start = time.time()
            text = t.transcribe(samples)
            duration = time.time() - start
            sys.stderr.write(
                f"[{len(samples) / audio.WHISPER_RATE:.1f}s of audio transcribed in {duration:.2f}s]\n"
            )
            sys.stderr.flush()
        print(text)

    elif cmd == "mic-test":
        t = load_transcriber(args.model, args.language, args.threads)
        rec = audio.Recorder(args.device)
        rec.start()
        sys.stderr.write(f"Recording for {args.seconds}s... speak now.\n")
        sys.stderr.flush()
        time.sleep(args.seconds)
        samples = rec.stop()
        level = audio.rms(samples)
        secs = len(samples) / 16000.0
        sys.stderr.write(f"Captured {secs:.1f}s, level {level:.4f} RMS\n")
        sys.stderr.flush()
        if args.save:
            audio.save_wav(args.save, samples)
            sys.stderr.write(f"Saved {args.save}\n")
            sys.stderr.flush()
        start = time.time()
        text = "" if audio.is_silence(samples) else t.transcribe(samples)
        sys.stderr.write(f"[transcribed in {time.time() - start:.2f}s]\n")
        sys.stderr.flush()
        print(text)

    elif cmd == "run":
        dictate(
            model_name=args.model,
            language=args.language,
            threads=args.threads,
            hotkey_spec=getattr(args, "hotkey", "ctrl+win"),
            device=getattr(args, "device", None),
            use_typing=getattr(args, "type", False),
            no_sound=getattr(args, "no_sound", False),
            fake_mic=getattr(args, "fake_mic", None),
        )


if __name__ == "__main__":
    main()
