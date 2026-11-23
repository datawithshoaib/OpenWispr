from __future__ import annotations

from pathlib import Path
from queue import Queue

import numpy as np

from openwispr import audio, platform_win
from openwispr.config import DesktopConfig
from openwispr.desktop_app import DesktopApp
from openwispr.dictation import DictationModule
from openwispr.hotkey import Action, Chord
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
        dictation_factory=lambda: DictationModule(
            chord_listener=WindowsChordListener(config.chord),
            microphone=_build_microphone(config),
            transcriber=transcriber,
            inserter=TextInserter(config.use_typing),
            cues=SilentSoundCues() if config.no_sound else WindowsSoundCues(),
            debug=config.debug,
        ),
        instance_guard=instance_guard if instance_guard is not None else SessionInstanceGuard(),
    )
