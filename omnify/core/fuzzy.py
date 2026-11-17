"""Small dependency-free fuzzy matcher used by the command palette.

The scoring favours, in order: an exact prefix, matches at word boundaries,
consecutive characters and short haystacks.  It is intentionally simple and
predictable — users should be able to find "New note" with ``nn`` and
"Timer: Start" with ``tst`` without learning a query language.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple


def _is_boundary(text: str, index: int) -> bool:
    if index <= 0:
        return True
    previous = text[index - 1]
    return previous in " \t-_/.:>|"


def fuzzy_score(query: str, text: str) -> Optional[int]:
    """Return a positive score when *query* fuzzily matches *text*.

    Matching is case-insensitive and subsequence-based.  ``None`` means the
    query does not match.  A score of zero is never returned for a match.
    """
    needle = "".join(str(query or "").casefold().split())
    haystack = str(text or "").casefold()
    if not needle:
        return 1
    if not haystack:
        return None
    if needle in haystack:
        start = haystack.index(needle)
        score = 1200 - start * 2
        if start == 0:
            score += 350
        if _is_boundary(haystack, start):
            score += 120
        score -= max(0, len(haystack) - len(needle))
        return max(1, score)

    # Multi-word queries should behave as if spaces were ignored, so
    # "new note" beats a short accidental substring such as "note".
    compact_needle = "".join(needle.split())
    compact_haystack = "".join(haystack.split())
    if compact_needle and compact_needle in compact_haystack:
        start = compact_haystack.index(compact_needle)
        score = 1150 - start * 2
        if start == 0:
            score += 350
        score -= max(0, len(compact_haystack) - len(compact_needle))
        return max(1, score)

    # Subsequence match: every query character must appear in order.
    position = -1
    score = 0
    consecutive = 0
    for char in needle:
        found = haystack.find(char, position + 1)
        if found < 0:
            return None
        if found == position + 1:
            consecutive += 1
            score += 16 + consecutive * 8
        else:
            consecutive = 0
            score += 4
        if _is_boundary(haystack, found):
            score += 18
        position = found
    score -= len(haystack) // 8
    score -= position // 4
    return max(1, score)


def best_score(query: str, values: Iterable[str]) -> Optional[int]:
    """Best score across several searchable fields (title, description…)."""
    scores = [fuzzy_score(query, value) for value in values if value]
    scores = [score for score in scores if score is not None]
    return max(scores) if scores else None


def rank(
    query: str,
    items: Sequence[Tuple[object, Sequence[str]]],
) -> List[Tuple[int, object]]:
    """Rank ``(item, searchable_fields)`` pairs by fuzzy score."""
    ranked: List[Tuple[int, object]] = []
    for item, fields in items:
        score = best_score(query, fields)
        if score is not None:
            ranked.append((score, item))
    ranked.sort(key=lambda pair: pair[0], reverse=True)
    return ranked
