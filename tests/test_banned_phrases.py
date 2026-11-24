"""
Tests for banned-phrase detection and sentence filtering.

Covers check_banned_phrases(), _drop_banned_sentences(), and
_normalize_for_phrase_match().
"""

import pytest

from leasing.note_services import (
    BANNED_PHRASES,
    _drop_banned_sentences,
    _normalize_for_phrase_match,
    check_banned_phrases,
)


# ---------------------------------------------------------------------------
# _normalize_for_phrase_match
# ---------------------------------------------------------------------------

class TestNormalize:
    def test_lowercase(self):
        assert _normalize_for_phrase_match("To Be Honest") == "to be honest"

    def test_curly_single_quotes_to_straight(self):
        result = _normalize_for_phrase_match("I\u2019ll be frank")
        assert result == "i'll be frank"

    def test_curly_double_quotes_to_straight(self):
        result = _normalize_for_phrase_match("\u201cfrankly\u201d")
        assert result == '"frankly"'

    def test_collapse_whitespace(self):
        result = _normalize_for_phrase_match("to  be   honest")
        assert result == "to be honest"

    def test_collapse_tabs_and_newlines(self):
        result = _normalize_for_phrase_match("to\tbe\nhonest")
        assert result == "to be honest"


# ---------------------------------------------------------------------------
# check_banned_phrases
# ---------------------------------------------------------------------------

class TestCheckBannedPhrases:
    def test_exact_match(self):
        result = check_banned_phrases("We want to be direct with you about this unit.")
        assert "to be direct with you" in result

    def test_case_insensitive(self):
        result = check_banned_phrases("TO BE HONEST, leasing is slow.")
        assert "to be honest" in result

    def test_curly_quotes(self):
        result = check_banned_phrases("I\u2019ll be frank, the market is tough.")
        assert "i'll be frank" in result

    def test_no_match_returns_empty(self):
        result = check_banned_phrases("Leasing activity was steady this week.")
        assert result == []

    def test_empty_string(self):
        assert check_banned_phrases("") == []

    def test_none(self):
        assert check_banned_phrases(None) == []

    def test_multiple_matches(self):
        text = "To be honest, we want to be direct with you."
        result = check_banned_phrases(text)
        assert "to be honest" in result
        assert "to be direct with you" in result

    def test_frankly_standalone(self):
        result = check_banned_phrases("Frankly, we need a price reduction.")
        assert "frankly" in result

    def test_candidly_standalone(self):
        result = check_banned_phrases("Candidly, the numbers are not good.")
        assert "candidly" in result

    def test_whitespace_in_phrase(self):
        result = check_banned_phrases("To  be\nhonest, this is a concern.")
        assert "to be honest" in result

    def test_want_to_be_direct_flagged(self):
        result = check_banned_phrases("I want to be direct with you.")
        assert "want to be direct" in result or "direct with you" in result

    def test_directly_not_flagged(self):
        result = check_banned_phrases(
            "Showings went well and we will call you directly."
        )
        assert result == []

    def test_want_to_be_directly_not_flagged(self):
        result = check_banned_phrases(
            "We want to be directly involved in the showing."
        )
        assert result == []

    def test_want_to_be_direct_with_you_flagged(self):
        result = check_banned_phrases("We want to be direct with you.")
        assert any("direct" in p for p in result)

    def test_all_phrases_are_lowercase(self):
        """BANNED_PHRASES must be stored lowercase for matching to work."""
        for phrase in BANNED_PHRASES:
            assert phrase == phrase.lower(), f"Phrase not lowercase: {phrase!r}"


# ---------------------------------------------------------------------------
# _drop_banned_sentences
# ---------------------------------------------------------------------------

class TestDropBannedSentences:
    def test_removes_sentence_with_banned_phrase(self):
        text = "We want to be direct with you. Showings are up this week."
        result = _drop_banned_sentences(text)
        assert "direct" not in result
        assert "Showings are up" in result

    def test_keeps_clean_sentences(self):
        text = "Leasing is going well. Three tours completed."
        result = _drop_banned_sentences(text)
        assert "Leasing is going well" in result
        assert "Three tours completed" in result

    def test_empty_string(self):
        assert _drop_banned_sentences("") == ""

    def test_none(self):
        assert _drop_banned_sentences(None) is None

    def test_all_sentences_banned(self):
        text = "To be honest, things are slow. Frankly, we need action."
        result = _drop_banned_sentences(text)
        assert result == ""

    def test_preserves_sentence_after_banned(self):
        text = "To be honest, it has been hard. But we have two tours next week."
        result = _drop_banned_sentences(text)
        assert "two tours next week" in result
        assert "honest" not in result

    def test_question_mark_delimiter(self):
        text = "Frankly, should we reduce? The market says yes."
        result = _drop_banned_sentences(text)
        assert "Frankly" not in result
        assert "market says yes" in result

    def test_exclamation_delimiter(self):
        text = "To be honest, wow! Great progress this week."
        result = _drop_banned_sentences(text)
        assert "honest" not in result
        assert "Great progress" in result

    def test_market_is_telling_us_bullet_dropped(self):
        text = (
            "We had three tours this week.\n"
            "- The market is telling us our price may be working against us\n"
            "- We are following up with every lead."
        )
        result = _drop_banned_sentences(text)
        assert "market is telling" not in result
        assert "three tours" in result
        assert "following up" in result

    def test_paragraph_and_bullets_byte_identical_when_clean(self):
        """No banned phrase → output is byte-identical to input."""
        text = (
            "We had a productive week at the property.\n"
            "- 3 new leads came in\n"
            "- 4 calls and 107 texts sent\n"
            "- One showing was missed\n"
            "We are continuing to follow up."
        )
        assert _drop_banned_sentences(text) == text

    def test_banned_bullet_removed_others_preserved(self):
        """Only the banned bullet disappears; paragraph + other bullets stay on own lines."""
        text = (
            "We had a productive week.\n"
            "- 3 new leads came in\n"
            "- The market is telling us prices are too high\n"
            "- One showing completed"
        )
        result = _drop_banned_sentences(text)
        assert "market is telling" not in result
        assert result == (
            "We had a productive week.\n"
            "- 3 new leads came in\n"
            "- One showing completed"
        )

    def test_banned_sentence_mid_paragraph(self):
        """Banned sentence inside a paragraph is removed; rest of line stays joined."""
        text = "Three leads came in. To be honest, things are slow. We are optimistic."
        result = _drop_banned_sentences(text)
        assert "honest" not in result
        assert result == "Three leads came in. We are optimistic."

    def test_closing_line_after_bullets_preserved(self):
        """Action line after bullets stays on its own line."""
        text = (
            "Progress this week:\n"
            "- 3 new leads came in\n"
            "- To be honest, volume is low\n"
            "- One tour completed\n"
            "We are working to schedule more showings."
        )
        result = _drop_banned_sentences(text)
        lines = result.split("\n")
        assert lines[0] == "Progress this week:"
        assert lines[1] == "- 3 new leads came in"
        assert lines[2] == "- One tour completed"
        assert lines[3] == "We are working to schedule more showings."
        assert len(lines) == 4
