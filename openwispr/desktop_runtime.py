from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Callable

import numpy as np

from openwispr import audio, platform_win, tray
from openwispr.desktop_app import DesktopApp
from openwispr.hotkey import Action, Chord


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


class TextInserter:
    def __init__(self, use_typing: bool) -> None:
        self._use_typing = use_typing

    def insert(self, text: str) -> None:
        if self._use_typing:
            platform_win.type_text(text)
        else:
            platform_win.paste_text(text)


class TrayWindow:
    def __init__(self, chord_label: str) -> None:
        self._quit_queue: Queue[tray.TrayCommand] = Queue()
        self._tray = tray.spawn_tray(chord_label, self._quit_queue)

    def show_ready(self, chord_label: str) -> None:
        self._tray.send(tray.TrayCommand.StopListening)

    def show_listening(self) -> None:
        self._tray.send(tray.TrayCommand.StartListening)

    def focus(self) -> None:
        pass

    def run(self) -> None:
        while True:
            try:
                if self._quit_queue.get(timeout=0.2) is tray.TrayCommand.Quit:
                    return
            except Empty:
                continue

    def close(self) -> None:
        self._tray.send(tray.TrayCommand.Quit)


class NoOpInstanceGuard:
    def acquire(self) -> bool:
        return True

    def release(self) -> None:
        pass


class HotkeyDictationEngine:
    def __init__(
        self,
        *,
        chord: Chord,
        source: AudioSource,
        transcriber: object,
        sound: bool,
    ) -> None:
        self._chord = chord
        self._source = source
        self._transcriber = transcriber
        self._sound = sound
        self._action_queue: Queue[Action] = Queue()
        self._work_queue: Queue[np.ndarray | None] = Queue()
        self._on_action: Callable[[Action], None] | None = None
        self._on_text: Callable[[str], None] | None = None
        self._stopping = threading.Event()

    def start_listening(
        self,
        on_action: Callable[[Action], None],
        on_text: Callable[[str], None],
    ) -> None:
        self._on_action = on_action
        self._on_text = on_text
        self._stopping.clear()
        threading.Thread(target=self._worker, name="dictation-worker", daemon=True).start()
        platform_win.spawn_hotkey_listener(self._chord, self._action_queue)
        threading.Thread(target=self._forward_actions, name="dictation-actions", daemon=True).start()

    def start_recording(self) -> bool:
        try:
            self._source.start()
        except Exception as e:
            self._write(f"  microphone error: {e}\n")
            return False
        self._cue(platform_win.Cue.Start)
        self._write("● listening...\n")
        return True

    def stop_recording(self) -> None:
        if not self._source.is_recording():
            return
        self._cue(platform_win.Cue.Stop)
        samples = self._source.stop()
        if audio.is_silence(samples):
            self._write("  (too short or silent, ignored)\n")
        else:
            self._work_queue.put(samples)

    def cancel_recording(self) -> None:
        if not self._source.is_recording():
            return
        self._source.stop()
        self._cue(platform_win.Cue.Cancel)
        self._write("  cancelled\n")

    def stop_listening(self) -> None:
        self._stopping.set()
        self._work_queue.put(None)

    def _forward_actions(self) -> None:
        while not self._stopping.is_set():
            try:
                action = self._action_queue.get(timeout=0.2)
            except Empty:
                continue
            if self._stopping.is_set():
                return
            if self._on_action is not None:
                self._on_action(action)

    def _worker(self) -> None:
        while True:
            samples = self._work_queue.get()
            if samples is None:
                break
            secs = len(samples) / audio.WHISPER_RATE
            start = time.time()
            try:
                text = self._transcriber.transcribe(samples)
                if not text:
                    self._write("  (no speech detected)\n")
                else:
                    duration = time.time() - start
                    self._write(f"  {secs:.1f}s -> {duration:.2f}s: {text}\n")
                    if self._on_text is not None:
                        self._on_text(text)
            except Exception as e:
                self._write(f"  {e}\n")

    def _cue(self, cue: platform_win.Cue) -> None:
        if self._sound:
            platform_win.play_cue(cue)

    @staticmethod
    def _write(message: str) -> None:
        if sys.stderr is not None:
            sys.stderr.write(message)
            sys.stderr.flush()


def build_desktop_app(
    *,
    chord: Chord,
    transcriber: object,
    device: str | None,
    use_typing: bool,
    no_sound: bool,
    fake_mic: str | None,
) -> DesktopApp:
    source = AudioSource(fake_mic, device)
    engine = HotkeyDictationEngine(
        chord=chord,
        source=source,
        transcriber=transcriber,
        sound=not no_sound,
    )
    return DesktopApp(
        window=TrayWindow(chord.label),
        engine=engine,
        inserter=TextInserter(use_typing),
        instance_guard=NoOpInstanceGuard(),
        chord=chord,
    )
