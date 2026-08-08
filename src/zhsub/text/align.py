"""Character-level alignment of two strings via ``difflib``.

Used in two places:

* **S2 repair** — the LLM is asked to echo back the exact string it was sent with
  break markers inserted, but in practice it silently switches full-width to
  half-width forms, rewrites digits, and "corrects" perceived typos. Exact
  comparison plus blind retries does not fix any of that; re-aligning recovers
  the break positions from whatever still matches.
* **bench** — maps the reference character stream onto the predicted character
  stream so onset error can be measured per character, instead of comparing
  segment ``start`` times (which measures segmentation policy, not timestamp
  accuracy).
"""

from __future__ import annotations

from difflib import SequenceMatcher


def _matcher(a: str, b: str) -> SequenceMatcher:
    # autojunk=False is mandatory. By default SequenceMatcher treats any element
    # appearing in >1% of a sequence of 200+ elements as "junk". For Chinese that
    # discards the most common characters (的, 了, 是...) and wrecks the alignment.
    return SequenceMatcher(None, a, b, autojunk=False)


def index_map(a: str, b: str) -> dict[int, int]:
    """Map indices in ``a`` to indices in ``b`` across the matching blocks.

    Characters outside a matching block are simply absent from the result; the
    caller decides how to handle them.
    """
    out: dict[int, int] = {}
    for ai, bi, size in _matcher(a, b).get_matching_blocks():
        for off in range(size):
            out[ai + off] = bi + off
    return out


def similarity(a: str, b: str) -> float:
    return _matcher(a, b).ratio()


def recover_breaks(sent: str, resp: str, sep: str = "|") -> tuple[list[int], float]:
    """Extract sentence-break positions from an LLM response, as indices into the original.

    Args:
        sent: the string sent to the LLM (no break markers, no punctuation).
        resp: the string the LLM returned, with ``sep`` inserted.
        sep: the break marker character.

    Returns:
        ``(breaks, ratio)`` where ``breaks`` is an ascending list of indices into
        ``sent``, each marking the **start of a new segment**, and ``ratio`` is
        the similarity between what was sent and what came back with ``sep``
        stripped. A ratio of 1.0 means the LLM echoed the string intact.

    Breaks that land inside a non-matching region are dropped rather than guessed:
    losing a break is better than placing one wrongly and dragging a timestamp
    with it.
    """
    stripped_chars: list[str] = []
    breaks_in_resp: list[int] = []
    for ch in resp:
        if ch == sep:
            breaks_in_resp.append(len(stripped_chars))
        else:
            stripped_chars.append(ch)
    stripped = "".join(stripped_chars)

    def _clean(xs: list[int]) -> list[int]:
        return sorted({x for x in xs if 0 < x < len(sent)})

    if stripped == sent:
        return _clean(breaks_in_resp), 1.0

    m = index_map(stripped, sent)  # index in stripped response -> index in sent
    ratio = similarity(sent, stripped)
    mapped: list[int] = []
    for b in breaks_in_resp:
        if b in m:
            mapped.append(m[b])
        elif (b - 1) in m:
            # The break sits right after a character that did match — place it
            # immediately after that character's counterpart.
            mapped.append(m[b - 1] + 1)
        # otherwise: the break fell inside a region the LLM rewrote -> drop it
    return _clean(mapped), ratio
