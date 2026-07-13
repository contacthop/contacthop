"""Repetition guard: the last line of defense against degenerate model output.

LLMs — especially small self-hosted ones at low temperature — can fall into
repetition loops ("doom loops"). The harness can't fix the model, but it can
refuse to deliver obviously looping text to a human over SMS, email, or a
live phone call. Enforced in the outbound gateway alongside quiet hours, rate
limits, and consent; disable with CONTACTHOP_REPETITION_GUARD=false.

Heuristics are deliberately conservative: short messages are exempt (humans
say "no no no"), and only strong signals fire — a long single-character run,
one word repeated many times consecutively, or a long message whose trigrams
are mostly copies of each other.
"""

from __future__ import annotations

from itertools import groupby

# Below this many words, repetition is treated as intentional emphasis.
MIN_WORDS = 25
# One word this many times in a row is a loop, not emphasis.
MAX_WORD_RUN = 10
# A single character repeated this long is degenerate even in a short message.
MAX_CHAR_RUN = 60
# In a long message, if fewer than this fraction of trigrams are distinct,
# the text is mostly copies of itself.
MIN_DISTINCT_TRIGRAM_RATIO = 0.34


def degenerate_reason(text: str) -> str | None:
    """Why the text looks like a repetition loop, or None if it looks fine."""
    for char, run in ((c, len(list(g))) for c, g in groupby(text)):
        if run > MAX_CHAR_RUN and not char.isspace():
            return f"the character {char!r} repeats {run} times in a row"

    words = text.split()
    if len(words) < MIN_WORDS:
        return None

    for word, group in groupby(w.lower() for w in words):
        run = len(list(group))
        if run >= MAX_WORD_RUN:
            return f"the word {word!r} repeats {run} times in a row"

    trigrams = list(zip(words, words[1:], words[2:], strict=False))
    ratio = len(set(trigrams)) / len(trigrams)
    if ratio < MIN_DISTINCT_TRIGRAM_RATIO:
        return f"only {ratio:.0%} of the message's trigrams are distinct"
    return None
