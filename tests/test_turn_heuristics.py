"""Tests for sounds_unfinished (app/voice/turn_heuristics.py)."""
from app.voice.turn_heuristics import sounds_unfinished


class TestExistingSignals:
    def test_empty_or_none_is_finished(self):
        assert sounds_unfinished(None) is False
        assert sounds_unfinished("") is False
        assert sounds_unfinished("   ") is False

    def test_trailing_filler_is_unfinished(self):
        assert sounds_unfinished("what time works for you um") is True
        assert sounds_unfinished("I was thinking uh") is True

    def test_trailing_connector_phrase_is_unfinished(self):
        assert sounds_unfinished("book it for tomorrow, you know") is True
        assert sounds_unfinished("let's see, i mean") is True

    def test_trailing_punctuation_is_unfinished(self):
        assert sounds_unfinished("book it for tomorrow,") is True
        assert sounds_unfinished("book it for tomorrow -") is True

    def test_complete_question_is_finished(self):
        assert sounds_unfinished("what is your leave policy?") is False
        assert sounds_unfinished("book me for tomorrow at noon") is False


class TestTrailingNumbers:
    """A caller's turn ending on a bare number (word or digit) is usually
    mid-number - a quarter-past time like "nine ... fifteen" being the
    motivating case."""

    def test_trailing_number_word_is_unfinished(self):
        assert sounds_unfinished("book it for nine") is True
        assert sounds_unfinished("my number is four two") is True

    def test_trailing_digit_is_unfinished(self):
        assert sounds_unfinished("book it for 9") is True
        assert sounds_unfinished("nine 15") is True

    def test_formatted_time_with_trailing_digit_is_unfinished(self):
        # Deliberately conservative: a smart-formatted "9:15" also ends in
        # a digit and gets the same small extension - costs only latency,
        # never correctness, on an utterance that really was complete.
        assert sounds_unfinished("book it for 9:15") is True

    def test_non_number_word_is_finished(self):
        assert sounds_unfinished("book it for noon") is False
        assert sounds_unfinished("that works for me") is False
