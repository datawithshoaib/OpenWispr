from openwispr.config import DesktopConfig
from openwispr.desktop_app import DesktopApp, Status
from openwispr.hotkey import Chord


class Window:
    def set_quit_handler(self, handler):
        self.handler = handler

    def show_status(self, status):
        self.status = status

    def run(self):
        pass

    def close(self):
        pass

    def focus(self):
        pass


class Engine:
    def start_listening(self, on_action, on_text):
        self.listening = True

    def is_busy(self):
        return False

    def stop_listening(self):
        pass


class Guard:
    def acquire(self):
        return True

    def focus_existing(self):
        return True

    def release(self):
        pass


def test_desktop_app_accepts_one_bundled_config_object():
    config = DesktopConfig(
        model_name="tiny.en",
        chord=Chord.parse("ctrl+win"),
        device=None,
        use_typing=False,
        no_sound=False,
        fake_mic=None,
    )
    window = Window()
    app = DesktopApp(
        window=window,
        engine=Engine(),
        inserter=object(),
        instance_guard=Guard(),
        config=config,
    )

    assert app.launch() is True
    assert window.status is Status.Idle
