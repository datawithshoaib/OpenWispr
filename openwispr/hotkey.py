"""
Push-to-talk hotkey logic, independent of the OS keyboard hook that feeds it.

Behaviour (modelled on Wispr Flow):
- Hold the chord to record; release it to transcribe and insert the text.
- Tap it quickly to go hands-free; press it again to stop. `Esc` cancels.
- Pressing any other key while holding it (e.g. Ctrl+Win+D) cancels, so the
  chord's modifiers still work for normal shortcuts.

Key codes are Windows virtual-key codes.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Callable

VK_ESCAPE: int = 0x1B
VK_LWIN: int = 0x5B
VK_RWIN: int = 0x5C
VK_LMENU: int = 0xA4
VK_RMENU: int = 0xA5

# A press shorter than this is a "tap" and starts hands-free mode.
TAP_THRESHOLD: float = 0.300  # 300 ms


class Action(Enum):
    Start = auto()
    Stop = auto()
    Cancel = auto()


@dataclass
class Outcome:
    action: Action | None = None
    # Swallow this key event so it doesn't reach the focused app.
    suppress: bool = False
    # Send a harmless dummy key before this event, so that releasing Win or Alt
    # doesn't open the Start menu or an app's menu bar.
    mask: bool = False


@dataclass(frozen=True)
class Chord:
    # Every group must have at least one key down; keys in a group are
    # alternatives (e.g. left or right Ctrl).
    groups: tuple[tuple[int, ...], ...]
    label: str

    @classmethod
    def parse(cls, spec: str) -> Chord:
        groups: list[tuple[int, ...]] = []
        for part in spec.split("+"):
            part_str = part.strip().lower()
            if not part_str:
                raise ValueError(f"empty key in hotkey '{spec}'")

            if part_str in ("ctrl", "control"):
                keys = (0xA2, 0xA3)
            elif part_str in ("lctrl", "left-ctrl"):
                keys = (0xA2,)
            elif part_str in ("rctrl", "right-ctrl"):
                keys = (0xA3,)
            elif part_str == "shift":
                keys = (0xA0, 0xA1)
            elif part_str in ("lshift", "left-shift"):
                keys = (0xA0,)
            elif part_str in ("rshift", "right-shift"):
                keys = (0xA1,)
            elif part_str == "alt":
                keys = (VK_LMENU, VK_RMENU)
            elif part_str in ("lalt", "left-alt"):
                keys = (VK_LMENU,)
            elif part_str in ("ralt", "right-alt", "altgr"):
                keys = (VK_RMENU,)
            elif part_str in ("win", "super", "meta"):
                keys = (VK_LWIN, VK_RWIN)
            elif part_str == "space":
                keys = (0x20,)
            elif part_str == "capslock":
                keys = (0x14,)
            elif part_str == "insert":
                keys = (0x2D,)
            elif part_str == "pause":
                keys = (0x13,)
            elif part_str == "scrolllock":
                keys = (0x91,)
            elif len(part_str) == 1 and part_str.isalnum():
                keys = (ord(part_str.upper()),)
            elif part_str.startswith("f") and part_str[1:].isdigit():
                num = int(part_str[1:])
                if 1 <= num <= 24:
                    keys = (0x70 + num - 1,)
                else:
                    raise ValueError(f"unknown key '{part_str}' in hotkey '{spec}'")
            else:
                raise ValueError(f"unknown key '{part_str}' in hotkey '{spec}'")

            groups.append(keys)

        return cls(groups=tuple(groups), label=spec)

    def contains(self, vk: int) -> bool:
        return any(vk in g for g in self.groups)

    def held(self, down: set[int]) -> bool:
        return all(any(k in down for k in g) for g in self.groups)


def is_modifier(vk: int) -> bool:
    return (0x10 <= vk <= 0x12) or (0xA0 <= vk <= 0xA5) or vk in (VK_LWIN, VK_RWIN)


class _ModeKind(Enum):
    IDLE = auto()
    HOLDING = auto()
    HANDS_FREE = auto()
    WAIT_RELEASE = auto()


@dataclass
class _Mode:
    kind: _ModeKind
    since: float = 0.0


class Machine:
    def __init__(self, chord: Chord) -> None:
        self.chord = chord
        self.down: set[int] = set()
        self.mode = _Mode(_ModeKind.IDLE)
        # The chord fired during the current press, so releasing Win/Alt needs masking.
        self.fired = False

    def is_active(self) -> bool:
        return self.mode.kind in (_ModeKind.HOLDING, _ModeKind.HANDS_FREE)

    def retain_down(self, still_down: Callable[[int], bool]) -> None:
        """
        Forgets keys the OS says are no longer pressed (guards against key-up
        events that never arrived, e.g. across the UAC secure desktop).
        """
        self.down = {vk for vk in self.down if still_down(vk)}

    def on_key(self, vk: int, is_down: bool, now: float) -> Outcome:
        repeat = is_down and (vk in self.down)
        if is_down:
            self.down.add(vk)
        else:
            self.down.discard(vk)

        in_chord = self.chord.contains(vk)
        held = self.chord.held(self.down)
        out = Outcome()

        if self.mode.kind == _ModeKind.IDLE:
            if is_down and in_chord and held:
                self.mode = _Mode(_ModeKind.HOLDING, since=now)
                self.fired = True
                out.action = Action.Start

        elif self.mode.kind == _ModeKind.HOLDING:
            if is_down and not in_chord and not repeat:
                self.mode = _Mode(_ModeKind.WAIT_RELEASE)
                out.action = Action.Cancel
            elif not held:
                if (now - self.mode.since) < TAP_THRESHOLD:
                    self.mode = _Mode(_ModeKind.HANDS_FREE)
                else:
                    self.mode = _Mode(_ModeKind.IDLE)
                    out.action = Action.Stop

        elif self.mode.kind == _ModeKind.HANDS_FREE:
            if is_down and vk == VK_ESCAPE:
                self.mode = _Mode(_ModeKind.WAIT_RELEASE)
                out.action = Action.Cancel
                out.suppress = True
            elif is_down and in_chord and held:
                self.mode = _Mode(_ModeKind.WAIT_RELEASE)
                self.fired = True
                out.action = Action.Stop

        elif self.mode.kind == _ModeKind.WAIT_RELEASE:
            any_chord_down = any(k in self.down for g in self.chord.groups for k in g)
            if not held and not any_chord_down:
                self.mode = _Mode(_ModeKind.IDLE)

        # Swallow chord non-modifier keys if chord is active or firing
        if in_chord and not is_modifier(vk) and (self.mode.kind != _ModeKind.IDLE or out.action is not None):
            out.suppress = True

        # Mask release of Win / Alt if fired
        if not is_down and in_chord and self.fired and vk in (VK_LWIN, VK_RWIN, VK_LMENU, VK_RMENU):
            out.mask = True

        any_chord_down = any(k in self.down for g in self.chord.groups for k in g)
        if not any_chord_down:
            self.fired = False

        return out
