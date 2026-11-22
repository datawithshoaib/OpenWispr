import time

from openwispr.desktop_app import DesktopApp, Status
from openwispr.hotkey import Action, Chord


class FakeWindow:
    def __init__(self, events=None):
        self.status = None
        self.statuses = []
        self.calls = events if events is not None else []
        self.closed = False
        self.focused = False
        self.quit_handler = None

    def show_status(self, status):
        self.status = status
        self.statuses.append(status)

    def set_quit_handler(self, handler):
        self.quit_handler = handler

    def focus(self):
        self.focused = True

    def run(self):
        pass

    def close(self):
        self.closed = True
        self.calls.append("close")


class FakeEngine:
    def __init__(self, transcript=None, start_ok=True, pending=False, finish_result=True, events=None):
        self.transcript = transcript
        self.start_ok = start_ok
        self.pending = pending
        self.finish_result = finish_result
        self.finish_timeout = None
        self.calls = events if events is not None else []
        self.listening = False
        self.recording = False
        self.busy = False
        self.on_action = None
        self.on_text = None

    def start_listening(self, on_action, on_text):
        self.listening = True
        self.on_action = on_action
        self.on_text = on_text

    def start_recording(self):
        self.recording = self.start_ok
        return self.start_ok

    def is_busy(self):
        return self.recording or self.busy

    def stop_recording(self):
        self.recording = False
        if self.transcript is None:
            self.on_text("")
        elif not self.pending:
            self.busy = True
            self.on_text(self.transcript)
            self.busy = False

    def finish(self, timeout):
        self.finish_timeout = timeout
        self.recording = False
        if self.finish_result and self.transcript is not None:
            self.busy = True
            self.on_text(self.transcript)
            self.busy = False
        return self.finish_result

    def cancel_recording(self):
        self.recording = False

    def stop_listening(self):
        self.listening = False
        self.calls.append("stop_listening")


class FakeInserter:
    def __init__(self):
        self.inserted = []

    def insert(self, text):
        self.inserted.append(text)


class FakeInstanceGuard:
    def __init__(self, acquired=True):
        self.acquired = acquired
        self.acquire_count = 0
        self.focus_count = 0
        self.released = False

    def acquire(self):
        self.acquire_count += 1
        return self.acquired

    def focus_existing(self):
        self.focus_count += 1

    def release(self):
        self.released = True


def make_app(
    acquired=True,
    transcript=None,
    start_ok=True,
    pending=False,
    finish_result=True,
    debug=False,
):
    events = []
    window = FakeWindow(events)
    engine = FakeEngine(transcript, start_ok, pending, finish_result, events)
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


def test_launch_opens_window_at_idle_without_starting_dictation():
    app, window, engine, _, guard = make_app()

    launched = app.launch()

    assert launched is True
    assert guard.acquire_count == 1
    assert (window.status, engine.listening, engine.recording) == (Status.Idle, True, False)
    assert window.quit_handler is not None


def test_second_launch_focuses_existing_instance_without_starting_engine():
    app, window, engine, _, guard = make_app(acquired=False)

    launched = app.launch()

    assert launched is False
    assert (guard.focus_count, window.focused, guard.released) == (1, False, False)
    assert (engine.listening, window.statuses, window.quit_handler) == (False, [], None)


def test_refused_launch_does_not_build_window_or_engine():
    guard = FakeInstanceGuard(acquired=False)
    built = []
    app = DesktopApp(
        window_factory=lambda: built.append("window") or FakeWindow(),
        engine_factory=lambda: built.append("engine") or FakeEngine(),
        inserter=FakeInserter(),
        instance_guard=guard,
        chord=Chord.parse("ctrl+win"),
    )

    launched = app.launch()

    assert launched is False
    assert built == []
    assert guard.focus_count == 1


def test_chord_start_begins_dictation():
    app, window, engine, _, _ = make_app()
    app.launch()

    engine.on_action(Action.Start)

    assert (engine.recording, window.status) == (True, Status.Listening)


def test_stop_shows_transcribing_then_inserts_text_once_and_returns_to_idle():
    app, window, engine, inserter, _ = make_app(transcript="hello world")
    app.launch()
    engine.on_action(Action.Start)

    engine.on_action(Action.Stop)

    assert (engine.recording, window.status, inserter.inserted) == (
        False,
        Status.Idle,
        ["hello world "],
    )
    assert Status.Transcribing in window.statuses


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

    assert (engine.recording, window.status, inserter.inserted) == (False, Status.Idle, [])


def test_quit_ends_the_desktop_app_instance():
    app, window, engine, _, guard = make_app()
    app.launch()

    app.run()

    assert (engine.listening, window.closed, guard.released) == (False, True, True)


def test_window_close_finishes_dictation_before_quitting():
    app, window, engine, inserter, guard = make_app(transcript="hello world", pending=True)
    app.launch()
    engine.on_action(Action.Start)

    window.quit_handler()

    assert window.statuses == [Status.Idle, Status.Listening, Status.Finishing]
    assert inserter.inserted == ["hello world "]
    assert engine.finish_timeout == 30.0
    assert engine.calls.index("stop_listening") < window.calls.index("close")
    assert (window.closed, guard.released) == (True, True)


def test_empty_transcript_returns_window_to_idle_without_insertion():
    app, window, engine, inserter, _ = make_app()
    app.launch()
    engine.on_action(Action.Start)

    engine.on_action(Action.Stop)

    assert (window.status, inserter.inserted) == (Status.Idle, [])


def test_finish_timeout_logs_and_still_quits(capsys):
    app, window, engine, inserter, guard = make_app(
        transcript="hello world", pending=True, finish_result=False
    )
    app.launch()
    engine.on_action(Action.Start)

    window.quit_handler()

    assert engine.finish_timeout == 30.0
    assert inserter.inserted == []
    assert (window.closed, guard.released) == (True, True)
    assert "30" in capsys.readouterr().err


def test_microphone_failure_keeps_desktop_app_idle():
    app, window, engine, _, _ = make_app(start_ok=False)
    app.launch()

    engine.on_action(Action.Start)

    assert (engine.recording, window.status) == (False, Status.Idle)
