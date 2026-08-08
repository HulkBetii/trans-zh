"""CER and onset-error measurement.

**Onset error is measured per character, not by comparing segment ``start``
times.** paraformer splits sentences on VAD silence while faster-whisper splits on
a 30-second window; comparing segment starts between the two measures
*segmentation policy*, not *timestamp accuracy*. Instead the reference character
stream is aligned against the predicted character stream, and the time of the
**first character** of each reference cue is read off.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from zhsub.subtitle import Cue
from zhsub.text.align import index_map
from zhsub.text.normalize import normalize_for_align, normalize_for_cer


@dataclass(slots=True)
class OnsetResult:
    median_ms: float
    p90_ms: float
    within_200ms: float  # percentage
    within_500ms: float
    coverage: float  # percentage of reference cues that could be matched
    n_matched: int
    n_ref: int


def expand_tokens_to_chars(
    tokens: list[tuple[str, float, float]],
) -> tuple[list[str], list[float]]:
    """``[(text, start, end)]`` -> ``(characters, per-character times)``.

    Multi-character tokens — embedded English words, or whisper "words" which
    routinely bundle several Han characters — are interpolated linearly. There is
    no finer information available.
    """
    chars: list[str] = []
    times: list[float] = []
    for text, start, end in tokens:
        n = len(text)
        if n == 0:
            continue
        if n == 1:
            chars.append(text)
            times.append(start)
            continue
        step = (end - start) / n
        for k, ch in enumerate(text):
            chars.append(ch)
            times.append(start + k * step)
    return chars, times


def _normalize_keeping_times(
    chars: list[str], times: list[float]
) -> tuple[str, list[float]]:
    """Normalise character by character so the character/time mapping survives.

    Normalising the whole string at once changes its length (punctuation removed,
    NFKC folding) and ``times`` desynchronises immediately. A character that
    normalises to nothing drops its timestamp with it.
    """
    out_chars: list[str] = []
    out_times: list[float] = []
    for ch, t in zip(chars, times):
        norm = normalize_for_align(ch)
        if not norm:
            continue
        # t2s on a single Han character is essentially always one character; taking
        # the first keeps the one-to-one invariant between stream and time array.
        out_chars.append(norm[0])
        out_times.append(t)
    return "".join(out_chars), out_times


def compute_cer(ref_cues: list[Cue], hyp_text: str) -> float:
    """CER after applying **identical** normalisation to both sides."""
    import jiwer

    ref = normalize_for_cer("".join(c.text for c in ref_cues))
    hyp = normalize_for_cer(hyp_text)
    if not ref:
        raise ValueError("Reference rỗng sau khi chuẩn hoá")
    return float(jiwer.cer(ref, hyp))


def cues_after_a_real_pause(ref_cues: list[Cue], min_gap_sec: float = 0.08) -> list[bool]:
    """Flag reference cues whose start follows an actual silence.

    Auto-generated CC tracks (the common case for a downloaded reference) are
    wall-to-wall: each cue starts exactly where the previous one ended, so most
    "start times" are just where the tool chose to break the text, not where
    speech began. Only a cue preceded by a real gap forced the tool to detect an
    onset, so only those starts carry timing information worth measuring against.
    """
    flags = [True]
    for prev, cur in zip(ref_cues, ref_cues[1:]):
        flags.append(cur.start - prev.end >= min_gap_sec)
    return flags


def compute_onset(
    ref_cues: list[Cue],
    pred_chars: list[str],
    pred_times: list[float],
    only_after_pause: bool = False,
    min_gap_sec: float = 0.08,
) -> OnsetResult:
    """Onset error between reference and prediction, measured per character.

    With ``only_after_pause`` the measurement is restricted to cues that follow a
    real silence — see :func:`cues_after_a_real_pause` for why that matters when
    the reference is an auto-generated CC track.
    """
    keep = (
        cues_after_a_real_pause(ref_cues, min_gap_sec)
        if only_after_pause
        else [True] * len(ref_cues)
    )

    ref_chars: list[str] = []
    cue_first_index: list[tuple[int, float]] = []  # (first char index, reference time)
    for cue, usable in zip(ref_cues, keep):
        norm = normalize_for_align(cue.text)
        if not norm:
            continue
        if usable:
            cue_first_index.append((len(ref_chars), cue.start))
        ref_chars.extend(norm)

    ref_stream = "".join(ref_chars)
    pred_stream, pred_time_list = _normalize_keeping_times(pred_chars, pred_times)

    mapping = index_map(ref_stream, pred_stream)

    errors_ms: list[float] = []
    for first_idx, ref_start in cue_first_index:
        pred_idx = mapping.get(first_idx)
        if pred_idx is None or pred_idx >= len(pred_time_list):
            # The cue's first character did not match — drop it rather than guess.
            # Interpolating to a neighbouring character injects a few hundred ms of
            # noise, which is exactly the resolution this metric is trying to measure.
            continue
        errors_ms.append(abs(pred_time_list[pred_idx] - ref_start) * 1000.0)

    n_ref = len(cue_first_index)
    n_matched = len(errors_ms)
    if n_matched == 0:
        return OnsetResult(float("nan"), float("nan"), 0.0, 0.0, 0.0, 0, n_ref)

    ordered = sorted(errors_ms)
    p90 = ordered[min(len(ordered) - 1, int(round(0.9 * (len(ordered) - 1))))]
    return OnsetResult(
        median_ms=statistics.median(ordered),
        p90_ms=p90,
        within_200ms=100.0 * sum(e <= 200 for e in ordered) / n_matched,
        within_500ms=100.0 * sum(e <= 500 for e in ordered) / n_matched,
        coverage=100.0 * n_matched / n_ref,
        n_matched=n_matched,
        n_ref=n_ref,
    )
