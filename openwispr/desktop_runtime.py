from __future__ import annotations

import threading
import time
from pathlib import Path
from queue import Empty, Queue
from typing import Callable

import numpy as np

from openwispr import audio, platform_win
from openwispr.config import DesktopConfig
from openwispr.desktop_app import DesktopApp
from openwispr.dictation import Dictation
from openwispr.hotkey import Action, Chord
from openwispr.output import write_stderr
from openwispr.instance import SessionInstanceGuard
from openwispr.window import TkWindow


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


class WindowsChordListener:
    """Puts Chord actions from the Windows keyboard hook into a queue."""

    def __init__(self, chord: Chord) -> None:
        self._chord = chord

    def start(self, actions: Queue[Action]) -> None:
        platform_win.spawn_hotkey_listener(self._chord, actions)

    def stop(self) -> None:
        platform_win.stop_hotkey_listener()


class WindowsSoundCues:
    def play(self, cue: platform_win.Cue) -> None:
        platform_win.play_cue(cue)


class SilentSoundCues:
    def play(self, cue: platform_win.Cue) -> None:
        pass


class TextInserter:
    def __init__(self, use_typing: bool) -> None:
        self._use_typing = use_typing

    def insert(self, text: str) -> None:
        if self._use_typing:
            platform_win.type_text(text)
        else:
            platform_win.paste_text(text)


class HotkeyDictationEngine:
    def __init__(
        self,
        *,
        chord_listener: object,
        microphone: object,
        transcriber: object,
        cues: object,
    ) -> None:
        self._chord_listener = chord_listener
        self._source = microphone
        self._transcriber = transcriber
        self._cues = cues
        self._action_queue: Queue[Action] = Queue()
        self._work_queue: Queue[tuple[Dictation, np.ndarray] | None] = Queue()
        self._on_action: Callable[[Action], None] | None = None
        self._on_text: Callable[[str, Dictation], None] | None = None
        self._stopping = threading.Event()
        self._state = threading.Condition()
        self._recording = False
        self._dictation: Dictation | None = None
        self._pending = 0
        self._action_thread: threading.Thread | None = None
        self._worker_thread: threading.Thread | None = None

    def start_listening(
        self,
        on_action: Callable[[Action], None],
        on_text: Callable[[str, Dictation], None],
    ) -> None:
        self._on_action = on_action
        self._on_text = on_text
        self._stopping.clear()
        self._worker_thread = threading.Thread(
            target=self._worker, name="dictation-worker", daemon=True
        )
        self._worker_thread.start()
        self._chord_listener.start(self._action_queue)
        self._action_thread = threading.Thread(
            target=self._forward_actions, name="dictation-actions", daemon=True
        )
        self._action_thread.start()

    def start_recording(self, dictation: Dictation) -> bool:
        try:
            self._source.start()
        except Exception as e:
            self._write(f"  microphone error: {e}\n")
            return False
        with self._state:
            self._recording = True
            self._dictation = dictation
        self._cue(platform_win.Cue.Start)
        self._write("● listening...\n")
        return True

    def is_busy(self) -> bool:
        with self._state:
            return self._recording or self._pending > 0

    def finish(self, timeout: float) -> bool:
        self.stop_recording()
        deadline = time.monotonic() + timeout
        with self._state:
            while self._pending > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._state.wait(remaining)
        return True

    def stop_recording(self) -> None:
        with self._state:
            if not self._recording:
                return
            self._recording = False
            dictation = self._dictation
            self._dictation = None
        if dictation is None:
            return
        self._cue(platform_win.Cue.Stop)
        samples = self._source.stop()
        if audio.is_silence(samples):
            self._write("  (too short or silent, ignored)\n")
            if self._on_text is not None:
                self._on_text("", dictation)
        else:
            with self._state:
                self._pending += 1
            self._work_queue.put((dictation, samples))

    def cancel_recording(self) -> None:
        with self._state:
            if not self._recording:
                return
            self._recording = False
            self._dictation = None
        self._source.stop()
        self._cue(platform_win.Cue.Cancel)
        self._write("  cancelled\n")

    def stop_listening(self) -> None:
        self._stopping.set()
        self.stop_recording()
        self._work_queue.put(None)
        self._chord_listener.stop()
        self._join(self._action_thread)
        self._join(self._worker_thread)

    def _join(self, thread: threading.Thread | None) -> None:
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)

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
            work = self._work_queue.get()
            if work is None:
                break
            dictation, samples = work
            text = ""
            try:
                secs = len(samples) / audio.WHISPER_RATE
                start = time.time()
                text = self._transcriber.transcribe(samples)
                if not text:
                    self._write("  (no speech detected)\n")
                else:
                    duration = time.time() - start
                    self._write(f"  {secs:.1f}s -> {duration:.2f}s: {text}\n")
            except Exception as e:
                self._write(f"  {e}\n")
            try:
                if self._on_text is not None:
                    self._on_text(text, dictation)
            finally:
                with self._state:
                    self._pending -= 1
                    self._state.notify_all()

    def _cue(self, cue: platform_win.Cue) -> None:
        self._cues.play(cue)

    @staticmethod
    def _write(message: str) -> None:
        write_stderr(message)


def _build_microphone(config: DesktopConfig) -> object:
    if config.fake_mic:
        return FakeMicSource(Path(config.fake_mic))
    return audio.Recorder(config.device)


def build_desktop_app(
    *,
    config: DesktopConfig,
    transcriber: object,
    instance_guard: object | None = None,
) -> DesktopApp:
    return DesktopApp(
        window_factory=lambda: TkWindow(),
        engine_factory=lambda: HotkeyDictationEngine(
            chord_listener=WindowsChordListener(config.chord),
            microphone=_build_microphone(config),
            transcriber=transcriber,
            cues=SilentSoundCues() if config.no_sound else WindowsSoundCues(),
        ),
        inserter=TextInserter(config.use_typing),
        instance_guard=instance_guard if instance_guard is not None else SessionInstanceGuard(),
        config=config,
    )
