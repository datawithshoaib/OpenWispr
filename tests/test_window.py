import openwispr.window as window_module
from openwispr.desktop_app import Status


def test_window_exposes_status_and_quit_only(monkeypatch):
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

    view = window_module.TkWindow()
    view.show_status(Status.Listening)

    assert labels == ["Status"]
    assert status_values == [Status.Idle.label, Status.Listening.label]
    assert buttons == ["Quit"]
