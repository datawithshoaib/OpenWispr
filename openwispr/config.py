from __future__ import annotations

from dataclasses import dataclass

from openwispr.hotkey import Chord


@dataclass(frozen=True)
class DesktopConfig:
    model_name: str
    chord: Chord
    language: str | None = None
    threads: int | None = None
    device: str | None = None
    use_typing: bool = False
    no_sound: bool = False
    fake_mic: str | None = None
    debug: bool | None = None
