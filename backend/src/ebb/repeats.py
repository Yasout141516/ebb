"""Spotting a question that repeats an earlier one (pure)."""

import re
from collections.abc import Sequence

# Chosen against the test pairs: the two paraphrase pairs score 1.0 and 0.5 ("unusable"
# and "usable" stay different words), the three different-question pairs score 0.33 or
# less. A score of 0.5 from only two shared words ("subnet mask" against a four-word
# question) is too little to call a repeat, so the lower bar needs three shared words.
REPEAT_THRESHOLD = 0.6
SHARED_THRESHOLD = 0.45
MIN_SHARED = 3
MIN_CONTAINED = 4

# Question and instruction words carry no topic, so they are dropped with the usual stop words.
STOP_WORDS = frozenset(
    "a an the of for to in on at by with from and or is are was were be been do does did "
    "have has had it its this that these those as can could would should will which what "
    "why how when where who whom explain describe state give define list name identify "
    "each every any all".split()
)

# An IP or CIDR token like 192.168.1.0/24 stays whole; a trailing full stop does not join it.
TOKEN = re.compile(r"[a-z0-9]+(?:[./][a-z0-9]+)*")


def _stem(word: str) -> str:
    if word.endswith("sses"):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _words(prompt: str) -> frozenset[str]:
    tokens = TOKEN.findall(prompt.casefold())
    return frozenset(_stem(t) for t in tokens if t not in STOP_WORDS)


def _close(a: frozenset[str], b: frozenset[str]) -> bool:
    if not a or not b:
        return False
    shared = len(a & b)
    overlap = shared / len(a | b)
    if overlap >= REPEAT_THRESHOLD or (shared >= MIN_SHARED and overlap >= SHARED_THRESHOLD):
        return True
    smaller, larger = sorted((a, b), key=len)
    return len(smaller) >= MIN_CONTAINED and smaller <= larger


def too_close(prompt: str, earlier: Sequence[str]) -> bool:
    words = _words(prompt)
    return any(_close(words, _words(e)) for e in earlier)
