import pytest
from openwispr.hotkey import (
    Action,
    Chord,
    Machine,
    Outcome,
    VK_ESCAPE,
)

LCTRL = 0xA2
LWIN = 0x5B
KEY_D = 0x44
SPACE = 0x20


class Sim:
    def __init__(self, spec: str) -> None:
        self.m = Machine(Chord.parse(spec))
        self.t = 1000.0

    def wait(self, ms: int) -> None:
        self.t += ms / 1000.0

    def down(self, vk: int) -> Outcome:
        return self.m.on_key(vk, True, self.t)

    def up(self, vk: int) -> Outcome:
        return self.m.on_key(vk, False, self.t)


def test_parse_chords():
    c = Chord.parse("ctrl+win")
    assert c.groups == ((0xA2, 0xA3), (0x5B, 0x5C))
    assert Chord.parse("F9").groups == ((0x78,),)
    assert Chord.parse("f1").groups == ((0x70,),)
    assert Chord.parse("right-alt").groups == ((0xA5,),)
    assert Chord.parse("ctrl + shift + d").groups[2] == (0x44,)

    with pytest.raises(ValueError):
        Chord.parse("ctrl+banana")
    with pytest.raises(ValueError):
        Chord.parse("f25")
    with pytest.raises(ValueError):
        Chord.parse("ctrl+")


def test_hold_to_talk():
    s = Sim("ctrl+win")
    assert s.down(LCTRL).action is None
    assert s.down(LWIN).action == Action.Start

    s.wait(50)
    assert s.down(LWIN).action is None  # auto-repeat is ignored

    s.wait(1500)
    o = s.up(LWIN)
    assert o.action == Action.Stop
    assert o.mask, "releasing Win must be masked to keep the Start menu closed"
    assert s.up(LCTRL) == Outcome()

    # works again afterwards, pressing in the other order
    assert s.down(LWIN).action is None
    assert s.down(LCTRL).action == Action.Start


def test_plain_win_press_is_not_masked():
    s = Sim("ctrl+win")
    s.down(LWIN)
    assert s.up(LWIN) == Outcome()


def test_other_key_cancels():
    s = Sim("ctrl+win")
    s.down(LCTRL)
    s.down(LWIN)
    s.wait(100)
    o = s.down(KEY_D)
    assert o.action == Action.Cancel
    assert not o.suppress, "the user's shortcut must still reach Windows"
    s.wait(500)
    assert s.up(KEY_D).action is None
    assert s.up(LWIN).action is None
    assert s.up(LCTRL).action is None
    assert s.down(LCTRL).action is None
    assert s.down(LWIN).action == Action.Start


def test_tap_for_hands_free_then_tap_to_stop():
    s = Sim("ctrl+win")
    s.down(LCTRL)
    assert s.down(LWIN).action == Action.Start
    s.wait(120)
    assert s.up(LWIN).action is None  # quick tap: keep recording
    s.up(LCTRL)
    assert s.m.is_active()

    s.wait(3000)
    assert s.down(0x41).action is None  # typing doesn't stop hands-free
    s.up(0x41)

    s.down(LCTRL)
    assert s.down(LWIN).action == Action.Stop
    s.wait(80)
    assert s.up(LWIN).action is None
    s.up(LCTRL)
    assert not s.m.is_active()

    s.down(LCTRL)
    assert s.down(LWIN).action == Action.Start


def test_escape_cancels_hands_free():
    s = Sim("f9")
    s.down(0x78)
    s.wait(100)
    s.up(0x78)
    o = s.down(VK_ESCAPE)
    assert o.action == Action.Cancel
    assert o.suppress
    s.up(VK_ESCAPE)
    assert s.down(0x78).action == Action.Start


def test_non_modifier_chord_keys_are_suppressed():
    s = Sim("ctrl+space")
    assert not s.down(LCTRL).suppress
    o = s.down(SPACE)
    assert o.action == Action.Start
    assert o.suppress
    assert s.down(SPACE).suppress  # repeats too

    s.wait(1000)
    o = s.up(SPACE)
    assert o.action == Action.Stop
    assert o.suppress
    assert not s.up(LCTRL).suppress

    # a lone space is typed normally
    assert not s.down(SPACE).suppress
    assert not s.up(SPACE).suppress


def test_stale_keys_are_forgotten():
    s = Sim("ctrl+win")
    s.down(LCTRL)
    s.m.retain_down(lambda _: False)
    assert s.down(LWIN).action is None
