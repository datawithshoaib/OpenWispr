import openwispr.wave_indicator as indicator_module
from openwispr.wave_indicator import (
    BARS,
    BOTTOM_MARGIN,
    FRAME_MS,
    HEIGHT,
    WIDTH,
    WaveIndicator,
)


class FakeCanvas:
    def __init__(self, parent=None, **kwargs):
        self.kwargs = kwargs
        self.cleared = []
        self.drawn = []

    def pack(self):
        pass

    def delete(self, tag):
        self.cleared.append(tag)

    def create_polygon(self, points, **kwargs):
        self.drawn.append((points, kwargs))
        return len(self.drawn)


class FakeToplevel:
    instances = []

    def __init__(self, parent=None):
        self.parent = parent
        self.withdrawn = 0
        self.deiconified = 0
        self.lifted = 0
        self.destroyed = 0
        self.geometry_value = None
        self.attributes_calls = []
        self.after_calls = []
        self.cancelled = []
        FakeToplevel.instances.append(self)

    def withdraw(self):
        self.withdrawn += 1

    def deiconify(self):
        self.deiconified += 1

    def lift(self):
        self.lifted += 1

    def overrideredirect(self, value):
        self.overrideredirect_value = value

    def resizable(self, *args):
        pass

    def attributes(self, *args):
        self.attributes_calls.append(args)

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080

    def geometry(self, value):
        self.geometry_value = value

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))
        return f"after-{len(self.after_calls)}"

    def after_cancel(self, token):
        self.cancelled.append(token)

    def destroy(self):
        self.destroyed += 1


def make_indicator(monkeypatch):
    FakeToplevel.instances = []
    monkeypatch.setattr(indicator_module.tk, "Toplevel", FakeToplevel)
    monkeypatch.setattr(indicator_module.tk, "Canvas", FakeCanvas)
    monkeypatch.setattr(indicator_module.tk, "TclError", Exception)
    return WaveIndicator(parent=object())


def test_show_positions_bottom_center_draws_pill_and_schedules_animation(monkeypatch):
    indicator = make_indicator(monkeypatch)
    window = FakeToplevel.instances[0]

    indicator.show()

    assert window.withdrawn == 1
    assert window.deiconified == 1
    assert window.lifted == 1
    assert window.geometry_value == f"{WIDTH}x{HEIGHT}+{1920 // 2 - WIDTH // 2}+{1080 - HEIGHT - BOTTOM_MARGIN}"
    assert window.attributes_calls[0] == ("-topmost", True)
    assert window.after_calls[0][0] == FRAME_MS
    assert indicator._canvas.cleared == ["all"]
    assert len(indicator._canvas.drawn) == BARS + 1
    assert indicator._canvas.drawn[0][1]["outline"] == indicator_module.PILL_OUTLINE
    assert all(kwargs["fill"] == indicator_module.BAR_FILL for _, kwargs in indicator._canvas.drawn[1:])


def test_repeated_show_does_not_create_extra_animation_loops(monkeypatch):
    indicator = make_indicator(monkeypatch)
    window = FakeToplevel.instances[0]

    indicator.show()
    indicator.show()

    assert len(window.after_calls) == 1


def test_tick_advances_animation_and_reschedules(monkeypatch):
    indicator = make_indicator(monkeypatch)
    window = FakeToplevel.instances[0]
    indicator.show()
    first_draw = list(indicator._canvas.drawn)

    window.after_calls[0][1]()

    assert indicator._frame == 1
    assert len(window.after_calls) == 2
    assert indicator._canvas.cleared == ["all", "all"]
    assert len(indicator._canvas.drawn) == len(first_draw) * 2
    assert indicator._canvas.drawn[len(first_draw):] != first_draw


def test_hide_cancels_animation_and_withdraws(monkeypatch):
    indicator = make_indicator(monkeypatch)
    window = FakeToplevel.instances[0]
    indicator.show()

    indicator.hide()

    assert window.cancelled == ["after-1"]
    assert window.withdrawn == 2


def test_close_hides_and_destroys(monkeypatch):
    indicator = make_indicator(monkeypatch)
    window = FakeToplevel.instances[0]
    indicator.show()

    indicator.close()

    assert window.destroyed == 1
    assert window.withdrawn == 2


def test_wave_bar_heights_change_across_frames(monkeypatch):
    indicator = make_indicator(monkeypatch)

    def bar_heights():
        return [
            max(points[1::2]) - min(points[1::2])
            for points, kwargs in indicator._canvas.drawn
            if kwargs.get("fill") == indicator_module.BAR_FILL
        ]

    indicator.show()
    first_heights = bar_heights()
    indicator._frame = 12
    indicator._draw()
    later_heights = bar_heights()[BARS:]

    assert first_heights != later_heights
    assert all(0 < height <= 14 for height in first_heights + later_heights)
    assert all(height >= 7 for height in first_heights + later_heights)
