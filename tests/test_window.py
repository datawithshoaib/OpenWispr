import threading

import openwispr.window as window_module
from openwispr.desktop_app import Status


class FakeIndicator:
    def __init__(self, parent):
        self.parent = parent
        self.calls = []
        self.threads = set()

    def show(self):
        self.calls.append("show")
        self.threads.add(threading.get_ident())

    def hide(self):
        self.calls.append("hide")
        self.threads.add(threading.get_ident())

    def close(self):
        self.calls.append("close")


def make_view(monkeypatch):
    labels = []
    buttons = []
    status_values = []
    set_threads = set()

    class StringVar:
        def __init__(self, value):
            self.value = value
            status_values.append(value)

        def set(self, value):
            self.value = value
            status_values.append(value)
            set_threads.add(threading.get_ident())

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
        def __init__(self):
            self.scheduled = []

        def after(self, ms, func):
            self.scheduled.append(func)
            return f"after#{len(self.scheduled)}"

        def run_event_loop(self):
            due, self.scheduled = self.scheduled, []
            for func in due:
                func()

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
    view.set_threads = set_threads
    return view, indicators, labels, buttons, status_values


def run_event_loop(view):
    view._root.run_event_loop()


def test_window_exposes_status_quit_and_recording_indicator(monkeypatch):
    view, indicators, labels, buttons, status_values = make_view(monkeypatch)

    view.show_status(Status.Listening)
    view.show_status(Status.Transcribing)
    run_event_loop(view)

    assert labels == ["Status"]
    assert status_values == [Status.Idle.label, Status.Listening.label, Status.Transcribing.label]
    assert buttons == ["Quit"]
    assert len(indicators) == 1
    assert indicators[0].parent is view._root
    assert indicators[0].calls == ["show", "hide"]


def test_window_hides_indicator_on_idle_and_closes_it(monkeypatch):
    view, indicators, _, _, _ = make_view(monkeypatch)

    view.show_status(Status.Idle)
    run_event_loop(view)
    view.close()

    assert indicators[0].calls == ["hide", "close"]


def test_status_from_background_thread_is_applied_on_tk_thread(monkeypatch):
    view, indicators, _, _, status_values = make_view(monkeypatch)
    view.set_threads.clear()

    worker = threading.Thread(target=view.show_status, args=(Status.Listening,))
    worker.start()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert status_values == [Status.Idle.label]
    assert indicators[0].calls == []

    run_event_loop(view)

    assert status_values == [Status.Idle.label, Status.Listening.label]
    assert indicators[0].calls == ["show"]
    tk_thread = {threading.get_ident()}
    assert view.set_threads == tk_thread
    assert indicators[0].threads == tk_thread


def test_wave_pill_shows_only_while_listening(monkeypatch):
    view, indicators, _, _, _ = make_view(monkeypatch)

    for status in (Status.Listening, Status.Transcribing, Status.Listening, Status.Finishing, Status.Idle):
        view.show_status(status)
        run_event_loop(view)

    assert indicators[0].calls == ["show", "hide", "show", "hide", "hide"]


def test_closing_stops_applying_statuses(monkeypatch):
    view, indicators, _, _, status_values = make_view(monkeypatch)

    view.close()
    view.show_status(Status.Listening)
    run_event_loop(view)

    assert indicators[0].calls == ["close"]
    assert status_values == [Status.Idle.label]
