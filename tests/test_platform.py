import io
import wave
from openwispr.platform_win import tone_wav


def test_cue_is_valid_wav():
    data = tone_wav([880.0, 1320.0])
    with wave.open(io.BytesIO(data), "rb") as w:
        assert w.getnchannels() == 1
        assert w.getframerate() == 22_050
        assert w.getnframes() == 22_050 * 45 // 1000 * 2


def test_play_cue_does_not_raise():
    from openwispr.platform_win import Cue, play_cue
    for cue in Cue:
        play_cue(cue)
