import threading
import time

import numpy as np
import pytest

from openwispr import audio, desktop_runtime, platform_win
from openwispr import desktop_app as desktop_app_module
from openwispr.config import DesktopConfig
from openwispr.desktop_app import DesktopApp, Status
from openwispr.desktop_runtime import HotkeyDictationEngine
from openwispr.hotkey import Action, Chord
from openwispr.platform_win import Cue

WAIT_TIMEOUT = 5.0
SPEECH = np.full(16_000, 0.1, dtype=np.float32)
SILENCE = np.zeros(16_000, dtype=np.float32)


def wait_until(condition, timeout=WAIT_TIMEOUT):
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for condition")
        time.sleep(0.005)


class FakeWindow:
    def __init__(self, events):
        self.statuses = []
        self.events = events
        self.closed = False
        self.focused = False
        self.quit_handler = None

    @property
    def status(self):
        return self.statuses[-1] if self.statuses else None

    def show_status(self, status):
        self.statuses.append(status)

    def set_quit_handler(self, handler):
        self.quit_handler = handler

    def focus(self):
        self.focused = True

    def run(self):
        pass

    def close(self):
        self.closed = True
        self.events.append("close window")


class FakeChordListener:
    def __init__(self, events):
        self.events = events
        self.listening = False
        self._actions = None

    def start(self, actions):
        self._actions = actions
        self.listening = True

    def stop(self):
        self.listening = False
        self.events.append("stop chord listener")

    def send(self, action):
        self._actions.put(action)


class FakeMicrophone:
    def __init__(self, samples=SPEECH, fails=False):
        self.samples = samples
        self.fails = fails
        self.recording = False

    def start(self):
        if self.fails:
            raise RuntimeError("no microphone found")
        self.recording = True

    def is_recording(self):
        return self.recording

    def stop(self):
        self.recording = False
        return self.samples.copy()


class FakeTranscriber:
    """Returns the given transcripts in order (repeating the last one). While
    held, transcriptions wait until the test releases them."""

    def __init__(self, *transcripts):
        self._transcripts = list(transcripts) or [""]
        self._released = threading.Event()
        self._released.set()

    def hold(self):
        self._released.clear()

    def release(self):
        self._released.set()

    def transcribe(self, samples):
        self._released.wait(WAIT_TIMEOUT)
        if len(self._transcripts) > 1:
            return self._transcripts.pop(0)
        return self._transcripts[0]


class FakeInserter:
    def __init__(self):
        self.inserted = []

    def insert(self, text):
        self.inserted.append(text)


class FakeSoundCues:
    def __init__(self):
        self.played = []

    def play(self, cue):
        self.played.append(cue)


class FakeInstanceGuard:
    def __init__(self, acquired=True, focus_results=None):
        self.acquired = acquired
        self.focus_results = focus_results
        self.acquire_count = 0
        self.focus_count = 0
        self.released = False

    def acquire(self):
        self.acquire_count += 1
        return self.acquired

    def focus_existing(self):
        self.focus_count += 1
        if self.focus_results is None:
            return True
        return self.focus_results.pop(0) if self.focus_results else True

    def release(self):
        self.released = True


class Harness:
    def __init__(self, *, acquired, focus_results, transcriber, microphone, debug):
        self.events = []
        self.built = []
        self.window = FakeWindow(self.events)
        self.chord_listener = FakeChordListener(self.events)
        self.microphone = microphone
        self.transcriber = transcriber
        self.inserter = FakeInserter()
        self.cues = FakeSoundCues()
        self.guard = FakeInstanceGuard(acquired, focus_results)
        self.app = DesktopApp(
            window_factory=self._build_window,
            engine_factory=self._build_engine,
            inserter=self.inserter,
            instance_guard=self.guard,
            config=DesktopConfig(model_name="tiny.en", chord=Chord.parse("ctrl+win"), debug=debug),
        )

    def _build_window(self):
        self.built.append("window")
        return self.window

    def _build_engine(self):
        self.built.append("engine")
        return HotkeyDictationEngine(
            chord_listener=self.chord_listener,
            microphone=self.microphone,
            transcriber=self.transcriber,
            cues=self.cues,
        )

    @property
    def inserted(self):
        return self.inserter.inserted

    def send(self, action):
        self.chord_listener.send(action)

    def dictate(self):
        """Starts a Dictation and Stops it once the window shows Listening."""
        self.start()
        self.stop()

    def start(self):
        listening = self.window.statuses.count(Status.Listening)
        self.send(Action.Start)
        wait_until(lambda: self.window.statuses.count(Status.Listening) > listening)

    def stop(self):
        transcribing = self.window.statuses.count(Status.Transcribing)
        self.send(Action.Stop)
        wait_until(lambda: self.window.statuses.count(Status.Transcribing) > transcribing)

    def wait_for_inserted(self, count):
        wait_until(lambda: len(self.inserted) >= count)

    def wait_for_idle(self):
        wait_until(lambda: self.window.status is Status.Idle)


@pytest.fixture
def make_app():
    harnesses = []

    def make(
        acquired=True,
        focus_results=None,
        transcripts=("hello world",),
        microphone=None,
        debug=False,
    ):
        harness = Harness(
            acquired=acquired,
            focus_results=focus_results,
            transcriber=FakeTranscriber(*transcripts),
            microphone=microphone if microphone is not None else FakeMicrophone(),
            debug=debug,
        )
        harnesses.append(harness)
        return harness

    yield make
    for harness in harnesses:
        harness.transcriber.release()
        harness.app.quit()


def test_launch_opens_window_at_idle_without_starting_dictation(make_app):
    h = make_app()

    launched = h.app.launch()

    assert launched is True
    assert h.guard.acquire_count == 1
    assert (h.window.statuses, h.chord_listener.listening, h.microphone.recording) == ([Status.Idle], True, False)
    assert h.window.quit_handler is not None


def test_second_launch_focuses_existing_instance_without_starting_dictation(make_app):
    h = make_app(acquired=False)

    launched = h.app.launch()

    assert launched is False
    assert (h.guard.focus_count, h.window.focused, h.guard.released) == (1, False, False)
    assert (h.chord_listener.listening, h.window.statuses, h.window.quit_handler) == (False, [], None)


def test_second_launch_retries_focus_until_existing_window_is_ready(make_app, monkeypatch):
    sleeps = []
    monkeypatch.setattr(desktop_app_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    h = make_app(acquired=False, focus_results=[False, True])

    launched = h.app.launch()

    assert launched is False
    assert (h.guard.focus_count, sleeps) == (2, [desktop_app_module.FOCUS_RETRY_DELAY])
    assert h.guard.released is False


def test_refused_launch_does_not_build_window_or_dictation_engine(make_app):
    h = make_app(acquired=False)

    h.app.launch()

    assert h.built == []
    assert h.guard.focus_count == 1


def test_chord_start_begins_dictation(make_app):
    h = make_app()
    h.app.launch()

    h.start()

    assert (h.microphone.recording, h.window.status, h.cues.played) == (True, Status.Listening, [Cue.Start])


def test_stop_shows_transcribing_then_inserts_text_once_and_returns_to_idle(make_app):
    h = make_app(transcripts=["hello world"])
    h.app.launch()

    h.dictate()
    h.wait_for_inserted(1)
    h.wait_for_idle()

    assert (h.microphone.recording, h.inserted) == (False, ["hello world "])
    assert h.window.statuses == [Status.Idle, Status.Listening, Status.Transcribing, Status.Idle]
    assert h.cues.played == [Cue.Start, Cue.Stop]


def test_second_stop_does_not_insert_again(make_app):
    h = make_app(transcripts=["hello world"])
    h.app.launch()
    h.dictate()
    h.wait_for_inserted(1)

    h.stop()
    h.wait_for_idle()
    h.dictate()
    h.wait_for_inserted(2)

    assert h.inserted == ["hello world ", "hello world "]


def test_same_sentence_in_separate_dictations_is_inserted_twice(make_app):
    h = make_app(transcripts=["hello world"])
    h.app.launch()

    for count in (1, 2):
        h.dictate()
        h.wait_for_inserted(count)
    h.wait_for_idle()

    assert h.inserted == ["hello world ", "hello world "]


def test_overlapping_dictations_each_insert_once_in_spoken_order(make_app):
    h = make_app(transcripts=["first words", "second words"])
    h.app.launch()
    h.transcriber.hold()

    h.dictate()
    h.start()
    h.transcriber.release()
    h.wait_for_inserted(1)

    assert (h.inserted, h.window.status) == (["first words "], Status.Listening)

    h.stop()
    h.wait_for_inserted(2)
    h.wait_for_idle()

    assert h.inserted == ["first words ", "second words "]


def test_stop_without_a_dictation_inserts_nothing(make_app):
    h = make_app(transcripts=["hello world"])
    h.app.launch()

    h.stop()
    h.wait_for_idle()

    assert (h.window.statuses, h.inserted, h.cues.played) == ([Status.Idle, Status.Transcribing, Status.Idle], [], [])


def test_cancel_ends_dictation_without_insertion(make_app):
    h = make_app(transcripts=["hello world"])
    h.app.launch()
    h.start()

    h.send(Action.Cancel)
    h.wait_for_idle()

    assert (h.microphone.recording, h.inserted, h.cues.played) == (False, [], [Cue.Start, Cue.Cancel])


def test_empty_transcript_returns_window_to_idle_without_insertion(make_app):
    h = make_app(transcripts=[""])
    h.app.launch()

    h.dictate()
    h.wait_for_idle()

    assert h.inserted == []


def test_silent_recording_returns_window_to_idle_without_insertion(make_app):
    h = make_app(transcripts=["hallucinated words"], microphone=FakeMicrophone(samples=SILENCE))
    h.app.launch()

    h.dictate()
    h.wait_for_idle()

    assert h.inserted == []


def test_quit_ends_the_desktop_app_instance(make_app):
    h = make_app()
    h.app.launch()

    h.app.run()

    assert (h.chord_listener.listening, h.window.closed, h.guard.released) == (False, True, True)


def test_window_close_finishes_dictation_before_quitting(make_app):
    h = make_app(transcripts=["hello world"])
    h.app.launch()
    h.start()

    h.window.quit_handler()

    assert h.window.statuses == [Status.Idle, Status.Listening, Status.Finishing]
    assert h.inserted == ["hello world "]
    assert h.events == ["stop chord listener", "close window"]
    assert (h.window.closed, h.guard.released) == (True, True)


def test_finish_timeout_logs_and_still_quits(make_app, monkeypatch, capsys):
    monkeypatch.setattr(desktop_app_module, "FINISH_TIMEOUT", 0.1)
    h = make_app(transcripts=["hello world"])
    h.app.launch()
    h.start()
    h.transcriber.hold()

    h.window.quit_handler()

    assert h.inserted == []
    assert (h.window.closed, h.guard.released) == (True, True)
    assert "timed out after 0.1s" in capsys.readouterr().err


def test_microphone_failure_keeps_desktop_app_idle_and_logs_error(make_app, capsys):
    h = make_app(microphone=FakeMicrophone(fails=True))
    h.app.launch()
    logged = []

    h.send(Action.Start)
    wait_until(lambda: logged.append(capsys.readouterr().err) or "microphone error" in "".join(logged))

    assert (h.window.statuses, h.cues.played) == ([Status.Idle], [])


def test_nothing_is_logged_for_debugging_by_default(make_app, capsys):
    h = make_app(transcripts=["hello world"])
    h.app.launch()

    h.dictate()
    h.wait_for_inserted(1)

    assert "[openwispr]" not in capsys.readouterr().err


def test_debug_logs_chord_stop_transcription_and_insertion(make_app, capsys):
    h = make_app(transcripts=["hello world"], debug=True)
    h.app.launch()

    h.dictate()
    h.wait_for_inserted(1)

    logged = capsys.readouterr().err
    assert "chord action Start" in logged
    assert "chord action Stop" in logged
    assert "Stop: finishing dictation" in logged
    assert "transcription result: hello world" in logged
    assert "insertion call: 'hello world'" in logged


@pytest.mark.parametrize(("no_sound", "expected_cues"), [(False, [Cue.Start, Cue.Stop]), (True, [])])
def test_built_desktop_app_dictates_from_fake_wav_microphone_with_windows_hook_and_tones(
    tmp_path, monkeypatch, no_sound, expected_cues
):
    wav = tmp_path / "speech.wav"
    audio.save_wav(wav, SPEECH)
    hook = {}
    played = []
    pasted = []
    window = FakeWindow([])
    monkeypatch.setattr(platform_win, "spawn_hotkey_listener", lambda chord, actions: hook.update(chord=chord, actions=actions))
    monkeypatch.setattr(platform_win, "stop_hotkey_listener", lambda: hook.update(stopped=True))
    monkeypatch.setattr(platform_win, "play_cue", played.append)
    monkeypatch.setattr(platform_win, "paste_text", pasted.append)
    monkeypatch.setattr(desktop_runtime, "TkWindow", lambda: window)
    config = DesktopConfig(
        model_name="tiny.en",
        chord=Chord.parse("ctrl+win"),
        fake_mic=str(wav),
        no_sound=no_sound,
    )
    app = desktop_runtime.build_desktop_app(
        config=config,
        transcriber=FakeTranscriber("hello world"),
        instance_guard=FakeInstanceGuard(),
    )

    app.launch()
    try:
        hook["actions"].put(Action.Start)
        wait_until(lambda: window.status is Status.Listening)
        hook["actions"].put(Action.Stop)
        wait_until(lambda: pasted)
    finally:
        app.quit()

    assert (hook["chord"], hook["stopped"]) == (config.chord, True)
    assert (pasted, played) == (["hello world "], expected_cues)
