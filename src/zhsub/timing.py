"""Shared cue-timing rules.

S2 (minimum duration) and S5 (character-rate relief) want the same thing: give a
cue more time on screen without stealing any from its neighbour. That is one
mechanism, so it lives in one place.

The governing idea is that **display time is not speech time**. A 0.3s utterance
cannot be stretched to 0.8s of speech, but its subtitle can stay up for 0.8s by
running into the silence that follows. What is never allowed is extending across
the next cue's first word — that desynchronises the line from the audio, which is
the one failure this pipeline exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass

# Leave a sliver of silence before the next cue so two lines never appear to
# collide on screen.
NEIGHBOUR_GUARD_SEC = 0.04


@dataclass(slots=True)
class Span:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


def merge_adjacent(
    spans: list[Span],
    max_gap_sec: float,
    max_duration_sec: float,
) -> list[list[int]]:
    """Group span indices that should become a single cue.

    Two spans merge when the silence between them is shorter than ``max_gap_sec``
    and the combined cue still fits inside ``max_duration_sec``. Returns groups of
    original indices so callers can merge the text alongside the timing.
    """
    if not spans:
        return []

    groups: list[list[int]] = [[0]]
    for i in range(1, len(spans)):
        prev = spans[groups[-1][0]]
        cur = spans[i]
        gap = cur.start - spans[groups[-1][-1]].end
        if gap < max_gap_sec and (cur.end - prev.start) <= max_duration_sec:
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


def extend_into_silence(
    spans: list[Span],
    index: int,
    target_duration_sec: float,
    audio_duration_sec: float,
    guard_sec: float = NEIGHBOUR_GUARD_SEC,
) -> float:
    """Return a new end time for ``spans[index]``, at most ``target_duration_sec`` long.

    Only silence after the cue is consumed, never any part of the next cue. The
    result may therefore still be shorter than requested — the caller decides
    whether that warrants a warning.
    """
    span = spans[index]
    if span.duration >= target_duration_sec:
        return span.end

    ceiling = audio_duration_sec
    if index + 1 < len(spans):
        ceiling = min(ceiling, spans[index + 1].start - guard_sec)

    wanted = span.start + target_duration_sec
    return max(span.end, min(wanted, ceiling))


def apply_min_duration(
    spans: list[Span],
    min_duration_sec: float,
    audio_duration_sec: float,
) -> list[bool]:
    """Stretch every too-short cue into the silence after it, in place.

    Returns a per-cue flag marking which ones were extended, so the caller can
    record it (S2 writes it into ``Segment.flags``).
    """
    extended = [False] * len(spans)
    for i, span in enumerate(spans):
        if span.duration >= min_duration_sec:
            continue
        new_end = extend_into_silence(spans, i, min_duration_sec, audio_duration_sec)
        if new_end > span.end:
            span.end = new_end
            extended[i] = True
    return extended


def cps(text: str, duration_sec: float) -> float:
    """Characters per second. Zero-length cues report infinity rather than dividing by zero."""
    if duration_sec <= 0:
        return float("inf")
    return len(text) / duration_sec


def relieve_cps(
    spans: list[Span],
    index: int,
    text: str,
    max_cps: float,
    audio_duration_sec: float,
) -> float:
    """New end time that would bring ``text`` under ``max_cps``, silence permitting.

    Same mechanism as :func:`extend_into_silence`; the only difference is that the
    target duration is derived from the text length instead of a fixed minimum.
    """
    if max_cps <= 0:
        return spans[index].end
    needed = len(text) / max_cps
    return extend_into_silence(spans, index, needed, audio_duration_sec)


def wrap_lines(text: str, max_chars_per_line: int, max_lines: int) -> list[str]:
    """Break a cue into display lines.

    Latin scripts wrap on spaces; Chinese has none, so it wraps anywhere. Returning
    more than ``max_lines`` is possible and deliberate: the caller warns rather than
    truncating, because the acceptance criteria require one cue per segment and
    forbid trimming the translation.
    """
    if not text:
        return [""]

    if " " in text.strip():
        lines: list[str] = []
        current = ""
        for word in text.split():
            candidate = f"{current} {word}".strip()
            if len(candidate) <= max_chars_per_line or not current:
                current = candidate
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
    else:
        lines = [
            text[i : i + max_chars_per_line] for i in range(0, len(text), max_chars_per_line)
        ]

    # Rebalance a two-line cue so the first line is not left nearly empty.
    if len(lines) == 2 and max_lines >= 2:
        total = sum(len(x) for x in lines)
        if total <= max_chars_per_line * 2 and len(lines[0]) < len(lines[1]) / 2:
            joined = " ".join(lines) if " " in text.strip() else "".join(lines)
            half = len(joined) // 2
            if " " in joined:
                pivot = joined.rfind(" ", 0, half + 1)
                if pivot > 0:
                    lines = [joined[:pivot], joined[pivot + 1 :]]
            else:
                lines = [joined[:half], joined[half:]]

    return lines
