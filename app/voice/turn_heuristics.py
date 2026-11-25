"""
Text-only heuristic for "did the caller actually finish talking?"

Pure string checks on the live partial transcript - no model, no extra
network call, no added latency. Meant to sit on top of silence-based VAD
endpointing: when the caller goes quiet, this decides whether the pause
looks like a real end-of-turn or a mid-sentence hesitation, so the pipeline
can grant a short extension instead of cutting them off.

Deliberately conservative - on anything ambiguous it returns False ("let the
turn end"), so it can only ever extend the wait, never shorten it.
"""

import re

# Public (no leading underscore): read by app/routers/admin.py to show the
# admin "Turn-taking" settings panel what this heuristic actually looks for,
# so the frontend never hardcodes a second copy of this list.
FILLER_WORDS = {
    # Only non-lexical disfluency sounds - Deepgram's *live* transcript lags
    # the actual audio by a word or two, so by the time our silence timer
    # fires, the partial routinely ends on an ordinary word ("the", "and",
    # "is"...) simply because recognition hasn't caught up yet, not because
    # the speaker paused there. That false signal was confirmed in testing:
    # a fully-finished question ("...leave policy?") showed its partial as
    # "...about the" right at decision time, wrongly extending the wait by
    # a full second on an already-complete turn. "um"/"uh" don't have that
    # problem - STT never hallucinates a disfluency sound that wasn't
    # actually spoken, so these are safe regardless of lag.
    "um", "uh", "umm", "uhh",
}

CONNECTOR_PHRASES = (
    "i mean", "you know", "i think", "i guess", "like i said",
    "sort of", "kind of", "let me think", "give me a second",
    "hold on", "one sec", "one second",
)

# A bare number sitting alone at the end of a partial is usually only half
# of a longer number a caller is still saying - a time ("nine" ... "fifteen"),
# a phone number, a price, a quantity. Those have a natural micro-pause
# between words that a single tight phrase ("nine-thirty", "ten o'clock")
# doesn't, so the plain silence timer can fire mid-number. Flagging it costs
# nothing when the caller really was done (one extra ~1s extension, capped),
# but recovers the rest of the number when they weren't.
NUMBER_WORDS = {
    "oh", "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
    "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
    "nineteen", "twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety",
    "hundred", "thousand", "quarter", "half",
}

_WORD_RE = re.compile(r"[a-z']+")
_TRAILING_DIGITS_RE = re.compile(r"\d\s*$")


def sounds_unfinished(text: str | None) -> bool:
    """Best-effort guess that `text` is a mid-thought fragment rather than
    a completed utterance."""
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    lowered = stripped.lower()

    if lowered.endswith(CONNECTOR_PHRASES):
        return True
    if stripped.endswith((",", "-", "–", "—")):
        return True
    if _TRAILING_DIGITS_RE.search(stripped):
        return True

    words = _WORD_RE.findall(lowered)
    if words and (words[-1] in FILLER_WORDS or words[-1] in NUMBER_WORDS):
        return True

    return False
