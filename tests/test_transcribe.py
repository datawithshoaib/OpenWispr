from openwispr.transcribe import (
    audio_ctx_for,
    clean_text,
    max_tokens_for,
)


def test_token_cap_scales_with_length():
    assert max_tokens_for(16_000 * 2) == 32
    assert max_tokens_for(16_000 * 10) == 96


def test_audio_ctx_scales_with_length():
    assert audio_ctx_for(16_000 * 3) == 150 + 64
    assert audio_ctx_for(16_000 * 10) == 500 + 64
    assert audio_ctx_for(16_000 * 29) is None  # close to full window
    assert audio_ctx_for(16_000 * 120) is None


def test_strips_annotations():
    assert clean_text(" [BLANK_AUDIO]") == ""
    assert clean_text("(music) Hello there.") == "Hello there."
    assert clean_text(" Hello *laughs* world ") == "Hello world"
    assert clean_text("[Music] .") == ""


def test_normalizes_whitespace():
    assert clean_text("  Hello,\n  how are   you? ") == "Hello, how are you?"


def test_collapses_runaway_repetition():
    assert clean_text("Music Music Music Music Music") == "Music"
    assert clean_text("no no no, it is very very good") == "no no no, it is very very good"


def test_collapses_repeated_phrases():
    assert (
        clean_text("Thank you. Thank you. Thank you. Thank you.")
        == "Thank you."
    )
    assert (
        clean_text("I am fine. I am fine. I am fine.")
        == "I am fine."
    )
    # Two repetitions is normal speech, should keep it.
    assert (
        clean_text("Thank you. Thank you.")
        == "Thank you. Thank you."
    )


def test_keeps_normal_text():
    assert clean_text(" It's 5 o'clock.") == "It's 5 o'clock."
    assert clean_text("Café naïve") == "Café naïve"


def test_silence_c_stderr_propagates_errors():
    import pytest
    from openwispr.transcribe import silence_c_stderr
    with pytest.raises(ValueError, match="boom"):
        with silence_c_stderr():
            raise ValueError("boom")
