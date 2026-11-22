import time

from openwispr.desktop_app import DesktopApp
from openwispr.hotkey import Action, Chord


class FakeWindow:
    def __init__(self):
        self.status = None
        self.closed = False
        self.focused = False

    def show_ready(self, chord_label):
        self.status = "ready"
        self.chord_label = chord_label

    def show_listening(self):
        self.status = "listening"

    def focus(self):
        self.focused = True

    def run(self):
        pass

    def close(self):
        self.closed = True


class FakeEngine:
    def __init__(self, transcript=None, start_ok=True):
        self.transcript = transcript
        self.start_ok = start_ok
        self.listening = False
        self.recording = False
        self.on_action = None
        self.on_text = None

    def start_listening(self, on_action, on_text):
        self.listening = True
        self.on_action = on_action
        self.on_text = on_text

    def start_recording(self):
        self.recording = self.start_ok
        return self.start_ok

    def stop_recording(self):
        self.recording = False
        if self.transcript is not None:
            self.on_text(self.transcript)

    def cancel_recording(self):
        self.recording = False

    def stop_listening(self):
        self.listening = False


class FakeInserter:
    def __init__(self):
        self.inserted = []

    def insert(self, text):
        self.inserted.append(text)


class FakeInstanceGuard:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.acquire_count = 0
        self.released = False

    def acquire(self):
        self.acquire_count += 1
        return self.acquired

    def release(self):
        self.released = True


def make_app(acquired=True, transcript=None, start_ok=True, debug=False):
    window = FakeWindow()
    engine = FakeEngine(transcript, start_ok)
    inserter = FakeInserter()
    guard = FakeInstanceGuard(acquired)
    app = DesktopApp(
        window=window,
        engine=engine,
        inserter=inserter,
        instance_guard=guard,
        chord=Chord.parse("ctrl+win"),
        debug=debug,
    )
    return app, window, engine, inserter, guard


def test_launch_makes_desktop_app_ready_without_starting_dictation():
    app, window, engine, _, _ = make_app()

    app.launch()

    assert (window.status, engine.listening, engine.recording) == ("ready", True, False)


def test_chord_start_begins_dictation():
    app, window, engine, _, _ = make_app()
    app.launch()

    engine.on_action(Action.Start)

    assert (engine.recording, window.status) == (True, "listening")


def test_stop_finishes_dictation_and_inserts_text_once():
    app, window, engine, inserter, _ = make_app(transcript="hello world")
    app.launch()
    engine.on_action(Action.Start)

    engine.on_action(Action.Stop)

    assert (engine.recording, window.status, inserter.inserted) == (
        False,
        "ready",
        ["hello world "],
    )


def test_repeated_transcript_is_not_inserted_twice():
    app, _, engine, inserter, _ = make_app(transcript="hello world")
    app.launch()
    engine.on_action(Action.Start)
    engine.on_action(Action.Stop)

    engine.on_text("hello world")

    assert inserter.inserted == ["hello world "]


def test_nothing_is_logged_by_default(capsys):
    app, _, engine, _, _ = make_app(transcript="hello world")
    app.launch()
    engine.on_action(Action.Start)

    engine.on_action(Action.Stop)

    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_second_stop_does_not_insert_again():
    app, _, engine, inserter, _ = make_app(transcript="hello world")
    app.launch()
    engine.on_action(Action.Start)

    engine.on_action(Action.Stop)
    engine.on_action(Action.Stop)

    assert inserter.inserted == ["hello world "]


def test_same_sentence_in_separate_dictations_is_inserted_twice():
    app, _, engine, inserter, _ = make_app(transcript="hello world")
    app.launch()

    for _ in range(2):
        engine.on_action(Action.Start)
        engine.on_action(Action.Stop)

    assert inserter.inserted == ["hello world ", "hello world "]


def test_stop_without_a_dictation_does_not_allow_insertion():
    app, _, engine, inserter, _ = make_app()
    app.launch()

    engine.on_action(Action.Stop)
    engine.on_text("stray words")

    assert inserter.inserted == []


def test_transcription_without_a_dictation_is_not_inserted():
    app, _, engine, inserter, _ = make_app()
    app.launch()

    engine.on_text("stray words")

    assert inserter.inserted == []


def test_debug_logs_chord_stop_transcription_and_insertion(capsys):
    app, _, engine, _, _ = make_app(transcript="hello world", debug=True)
    app.launch()
    engine.on_action(Action.Start)

    engine.on_action(Action.Stop)

    logged = capsys.readouterr().err
    assert "chord action Start" in logged
    assert "chord action Stop" in logged
    assert "Stop: finishing dictation" in logged
    assert "transcription result: hello world" in logged
    assert "insertion call:" in logged


def test_one_dictation_inserts_once_even_after_dedup_window(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    app, _, engine, inserter, _ = make_app(transcript="hello world")
    app.launch()
    engine.on_action(Action.Start)

    engine.on_action(Action.Stop)
    clock[0] += 10
    engine.on_text("hello world")

    assert inserter.inserted == ["hello world "]


def test_cancel_ends_dictation_without_insertion():
    app, window, engine, inserter, _ = make_app()
    app.launch()
    engine.on_action(Action.Start)

    engine.on_action(Action.Cancel)

    assert (engine.recording, window.status, inserter.inserted) == (False, "ready", [])


def test_quit_ends_the_desktop_app_instance():
    app, window, engine, _, guard = make_app()
    app.launch()

    app.run()

    assert (engine.listening, window.closed, guard.released) == (False, True, True)


def test_microphone_failure_keeps_desktop_app_ready():
    app, window, engine, _, _ = make_app(start_ok=False)
    app.launch()

    engine.on_action(Action.Start)

    assert (engine.recording, window.status) == (False, "ready")
