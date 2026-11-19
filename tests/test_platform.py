import io
import time
import wave
from openwispr.platform_win import tone_wav
from openwispr.tray import render_idle_icon, render_wave_icon, rgba_to_hicon, spawn_tray, TrayCommand


def test_cue_is_valid_wav():
    data = tone_wav([880.0, 1320.0])
    with wave.open(io.BytesIO(data), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 22_050
        assert w.getnframes() == 22_050 * 45 // 1000 * 2


def test_tray_icon_rendering():
    idle_bytes = render_idle_icon()
    assert len(idle_bytes) == 32 * 32 * 4
    hicon_idle = rgba_to_hicon(idle_bytes)
    assert hicon_idle is not None

    wave_bytes = render_wave_icon(0.5)
    assert len(wave_bytes) == 32 * 32 * 4
    hicon_wave = rgba_to_hicon(wave_bytes)
    assert hicon_wave is not None


def test_tray_lifecycle():
    tray = spawn_tray()
    time.sleep(0.05)
    tray.send(TrayCommand.StartListening)
    time.sleep(0.05)
    tray.send(TrayCommand.StopListening)
    time.sleep(0.05)
    tray.send(TrayCommand.Quit)
    time.sleep(0.05)



def test_play_cue_does_not_raise():
    from openwispr.platform_win import Cue, play_cue
    for cue in Cue:
        play_cue(cue)
