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

_TRAILING_FILLERS = {
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

_TRAILING_PHRASES = (
    "i mean", "you know", "i think", "i guess", "like i said",
    "sort of", "kind of", "let me think", "give me a second",
    "hold on", "one sec", "one second",
)

_WORD_RE = re.compile(r"[a-z']+")


def sounds_unfinished(text: str | None) -> bool:
    """Best-effort guess that `text` is a mid-thought fragment rather than
    a completed utterance."""
    if not text:
        return False
    stripped = text.strip()
    if not stripped:
        return False
    lowered = stripped.lower()

    if lowered.endswith(_TRAILING_PHRASES):
        return True
    if stripped.endswith((",", "-", "–", "—")):
        return True

    words = _WORD_RE.findall(lowered)
    if words and words[-1] in _TRAILING_FILLERS:
        return True

    return False
