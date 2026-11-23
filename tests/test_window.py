import openwispr.window as window_module
from openwispr.desktop_app import Status


class FakeIndicator:
    def __init__(self, parent):
        self.parent = parent
        self.calls = []

    def show(self):
        self.calls.append("show")

    def hide(self):
        self.calls.append("hide")

    def close(self):
        self.calls.append("close")


def make_view(monkeypatch):
    labels = []
    buttons = []
    status_values = []

    class StringVar:
        def __init__(self, value):
            self.value = value
            status_values.append(value)

        def set(self, value):
            self.value = value
            status_values.append(value)

    class Widget:
        def __init__(self, parent=None, **kwargs):
            self.kwargs = kwargs

        def pack(self, **kwargs):
            pass

    class Label(Widget):
        def __init__(self, parent=None, text=None, textvariable=None, **kwargs):
            super().__init__(parent, **kwargs)
            if text is not None:
                labels.append(text)

    class Button(Widget):
        def __init__(self, parent=None, text=None, command=None, **kwargs):
            super().__init__(parent, **kwargs)
            buttons.append(text)

    class Tk:
        def title(self, value):
            pass

        def geometry(self, value):
            pass

        def resizable(self, *args):
            pass

        def protocol(self, *args):
            pass

        def deiconify(self):
            pass

        def lift(self):
            pass

        def focus_force(self):
            pass

        def destroy(self):
            pass

    monkeypatch.setattr(window_module.tk, "Tk", Tk)
    monkeypatch.setattr(window_module.tk, "Frame", Widget)
    monkeypatch.setattr(window_module.tk, "Label", Label)
    monkeypatch.setattr(window_module.tk, "Button", Button)
    monkeypatch.setattr(window_module.tk, "StringVar", StringVar)

    indicators = []

    def indicator_factory(parent):
        indicator = FakeIndicator(parent)
        indicators.append(indicator)
        return indicator

    view = window_module.TkWindow(indicator_factory=indicator_factory)
    return view, indicators, labels, buttons, status_values


def test_window_exposes_status_quit_and_recording_indicator(monkeypatch):
    view, indicators, labels, buttons, status_values = make_view(monkeypatch)

    view.show_status(Status.Listening)
    view.show_status(Status.Transcribing)

    assert labels == ["Status"]
    assert status_values == [Status.Idle.label, Status.Listening.label, Status.Transcribing.label]
    assert buttons == ["Quit"]
    assert len(indicators) == 1
    assert indicators[0].parent is view._root
    assert indicators[0].calls == ["show", "hide"]


def test_window_hides_indicator_on_idle_and_closes_it(monkeypatch):
    view, indicators, _, _, _ = make_view(monkeypatch)

    view.show_status(Status.Idle)
    view.close()

    assert indicators[0].calls == ["hide", "close"]
