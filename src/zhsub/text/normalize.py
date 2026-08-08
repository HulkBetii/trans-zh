"""Chinese text normalisation, applied before measuring CER.

Skip these steps and CER measures noise rather than recognition quality:

* ``faster-whisper`` large-v3 often emits **traditional** characters while
  ``paraformer-zh`` emits simplified — hundreds of character mismatches for
  identical content.
* ``ct-punc`` inserts punctuation, hand-written references vary
  — punctuation would dominate the error rate.
* One engine writes "2024", the other writes "二零二四"
  — a systematic penalty that depends on how the reference happened to be typed.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

_ZH_DIGITS = "零一二三四五六七八九"

# CJK punctuation. The Latin side is handled by Unicode category, so only the
# CJK characters need listing explicitly.
_CJK_PUNCT = "。，、；：？！“”‘’（）《》〈〉【】「」『』…—～·﹏､｡"


@lru_cache(maxsize=1)
def _opencc_t2s():
    try:
        from opencc import OpenCC

        return OpenCC("t2s")
    except Exception:  # pragma: no cover - only hit when opencc is missing
        return None


def to_simplified(text: str) -> str:
    """Traditional to simplified. Returns the input unchanged if opencc is absent."""
    cc = _opencc_t2s()
    return cc.convert(text) if cc is not None else text


def to_halfwidth(text: str) -> str:
    """Full-width ASCII to half-width. ``NFKC`` also folds a few equivalent forms."""
    return unicodedata.normalize("NFKC", text)


def strip_punct(text: str) -> str:
    out = []
    for ch in text:
        if ch in _CJK_PUNCT:
            continue
        # P* = punctuation, S* = symbol
        if unicodedata.category(ch)[0] in ("P", "S"):
            continue
        out.append(ch)
    return "".join(out)


def digits_to_zh(text: str) -> str:
    """Rewrite Arabic digit runs as digit-by-digit Chinese ("2024" -> "二零二四").

    Normalising in one direction lets two engines with different conventions be
    compared at all.

    Known limitation: Chinese numerals expressing a *value* are left alone, so a
    reference reading "二十五" against ASR output "25" (-> "二五") still counts as
    an error. That case is rare and the penalty is small; going the other way
    would require inferring numeric values and is considerably more error-prone.
    """
    return re.sub(r"\d+", lambda m: "".join(_ZH_DIGITS[int(d)] for d in m.group()), text)


def collapse_space(text: str) -> str:
    return re.sub(r"\s+", "", text)


def normalize_for_cer(text: str) -> str:
    """The full normalisation chain, applied identically to reference and hypothesis."""
    text = to_halfwidth(text)
    text = to_simplified(text)
    text = strip_punct(text)
    text = digits_to_zh(text)
    text = collapse_space(text)
    return text.lower()


def normalize_for_align(text: str) -> str:
    """Lighter variant, used when aligning streams to look up timestamps.

    Preserves character count as far as possible, so it deliberately leaves
    digits alone: ``digits_to_zh`` lengthens the string and breaks the one-to-one
    mapping back onto the token array.
    """
    text = to_halfwidth(text)
    text = to_simplified(text)
    text = strip_punct(text)
    text = collapse_space(text)
    return text.lower()
