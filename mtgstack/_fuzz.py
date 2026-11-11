"""Pure-Python stand-in for the parts of rapidfuzz mtgstack uses (the phone/browser build has no rapidfuzz wheel).

Scores follow rapidfuzz's definitions (0-100). `process.extract` prefilters choices by shared character bigrams so
matching an OCR'd title against ~35k card names stays fast without C extensions.
"""
from __future__ import annotations

import re


def _lcs(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = [0] * (len(b) + 1)
    for ca in a:
        cur = [0]
        for j, cb in enumerate(b):
            cur.append(prev[j] + 1 if ca == cb else (cur[j] if cur[j] > prev[j + 1] else prev[j + 1]))
        prev = cur
    return prev[-1]


def _lev(a: str, b: str) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


class Levenshtein:
    @staticmethod
    def distance(a: str, b: str) -> int:
        return _lev(a, b)

    @staticmethod
    def normalized_similarity(a: str, b: str) -> float:
        m = max(len(a), len(b))
        return 1.0 if m == 0 else 1.0 - _lev(a, b) / m


class distance:  # noqa: N801 - mirrors `rapidfuzz.distance`
    Levenshtein = Levenshtein


class fuzz:  # noqa: N801 - mirrors `rapidfuzz.fuzz`
    @staticmethod
    def ratio(a: str, b: str, **_) -> float:
        n = len(a) + len(b)
        return 100.0 if n == 0 else 100.0 * 2 * _lcs(a, b) / n

    @staticmethod
    def partial_ratio(a: str, b: str, **_) -> float:
        s, l = (a, b) if len(a) <= len(b) else (b, a)
        if not s:
            return 100.0 if not l else 0.0
        return max(fuzz.ratio(s, l[i:i + len(s)]) for i in range(len(l) - len(s) + 1))

    @staticmethod
    def token_sort_ratio(a: str, b: str, **_) -> float:
        t = lambda x: " ".join(sorted(re.findall(r"\w+", x)))
        return fuzz.ratio(t(a), t(b))

    @staticmethod
    def WRatio(a: str, b: str, **_) -> float:  # noqa: N802
        r = fuzz.ratio(a, b)
        ls, ll = sorted((len(a), len(b)))
        if ls and ll / ls >= 1.5:
            r = max(r, 0.9 * fuzz.partial_ratio(a, b))
        return max(r, 0.95 * fuzz.token_sort_ratio(a, b))


def _bigrams(s: str) -> set[str]:
    s = f" {s.lower()} "
    return {s[i:i + 2] for i in range(len(s) - 1)}


_BIGRAM_CACHE: dict[int, tuple[list, list[set[str]]]] = {}


class process:  # noqa: N801 - mirrors `rapidfuzz.process`
    @staticmethod
    def extract(query: str, choices, scorer=None, limit: int = 5):
        scorer = scorer or fuzz.WRatio
        choices = list(choices) if not isinstance(choices, list) else choices
        key = id(choices)
        cached = _BIGRAM_CACHE.get(key)
        if not cached or cached[0] is not choices:
            cached = (choices, [_bigrams(c) for c in choices])
            _BIGRAM_CACHE.clear()
            _BIGRAM_CACHE[key] = cached
        grams = cached[1]
        q = _bigrams(query)
        if len(choices) > 400:
            overlap = sorted(range(len(choices)), key=lambda i: -len(q & grams[i]))[: max(200, limit * 40)]
        else:
            overlap = range(len(choices))
        scored = [(choices[i], float(scorer(query, choices[i])), i) for i in overlap]
        scored.sort(key=lambda t: -t[1])
        return scored[:limit]
