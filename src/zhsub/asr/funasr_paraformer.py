"""ASR via FunASR: paraformer-zh + fsmn-vad + ct-punc.

The timestamps produced here are the **single source of truth** for the whole
pipeline. No later stage may create or adjust timing, beyond extending a cue into
a silence that is already known.

The subtle part is pairing the ``timestamp`` array with the text: ``ct-punc``
inserts punctuation into the text, but punctuation has **no** timestamp of its
own, so zipping the two arrays directly misaligns them from the first character.
The text is therefore tokenised using FunASR's own convention (one Han character
per token, one contiguous Latin word per token, punctuation attached to the
preceding token) before the counts are compared.
"""

from __future__ import annotations

import logging
import unicodedata
from pathlib import Path

from .base import AsrOutput, AsrSentence, AsrToken

log = logging.getLogger(__name__)

_CJK_PUNCT = "。，、；：？！“”‘’（）《》〈〉【】「」『』…—～·﹏､｡"


def _is_punct(ch: str) -> bool:
    return ch in _CJK_PUNCT or unicodedata.category(ch)[0] in ("P", "S")


def _is_ascii_word(ch: str) -> bool:
    # The apostrophe is deliberately excluded here and handled in split_tokens,
    # where it only counts as a word character mid-word.
    return ch.isascii() and ch.isalnum()


def split_tokens(text: str) -> list[list[str]]:
    """Split punctuated text into ``[[token, trailing_punctuation], ...]``.

    Follows FunASR's convention for generating the ``timestamp`` array: one Han
    character is one token, one contiguous Latin word is one token, punctuation
    is not a token.
    """
    tokens: list[list[str]] = []
    latin = ""

    def flush() -> None:
        nonlocal latin
        if latin:
            tokens.append([latin, ""])
            latin = ""

    for ch in text:
        if ch.isspace():
            flush()
        elif _is_ascii_word(ch) or (ch == "'" and latin):
            # The apostrophe check must come BEFORE the punctuation check. "'" has
            # Unicode category Po, so testing punctuation first splits "don't" into
            # "don" + "t" and invents a token FunASR never emitted. On a 35-minute
            # clip containing English speech that was exactly 19 phantom tokens
            # (don't / i'm / that's / they're), breaking the token==timestamp
            # invariant. Requiring `latin` keeps a leading quote as punctuation.
            latin += ch
        elif _is_punct(ch):
            flush()
            if tokens:
                tokens[-1][1] += ch
            # leading punctuation with no preceding token -> discard
        else:
            flush()
            tokens.append([ch, ""])
    flush()
    return tokens


# Punctuation that ends a subtitle-sized unit. Chinese ASR output uses the comma
# heavily as a breath pause, so treating it as a boundary yields cue-sized units
# rather than paragraph-sized ones.
_BREAK_PUNCT = "。？！…，、；：?!,;:"


def _group_into_sentences(tokens: list[AsrToken], gap_sec: float) -> list[AsrSentence]:
    """Group a flat token list into sentences on punctuation or a silent gap.

    A gap break is needed as well as punctuation because ``ct-punc`` regularly
    misses a pause, which would otherwise produce one very long cue spanning an
    obvious silence.
    """
    sentences: list[AsrSentence] = []
    current: list[AsrToken] = []

    def flush() -> None:
        if not current:
            return
        text = "".join(t.text + (t.punct_after or "") for t in current)
        sentences.append(
            AsrSentence(text=text, start=current[0].start, end=current[-1].end, tokens=list(current))
        )
        current.clear()

    for tok in tokens:
        if current and tok.start - current[-1].end > gap_sec:
            flush()
        current.append(tok)
        if tok.punct_after and any(ch in _BREAK_PUNCT for ch in tok.punct_after):
            flush()
    flush()
    return sentences


def parse_funasr_result(res: list[dict], gap_sec: float = 0.5) -> AsrOutput:
    """Convert raw ``AutoModel.generate`` output into an :class:`AsrOutput`.

    Deliberately built from the **top-level** ``text`` and ``timestamp`` pair, and
    the ``sentence_info`` array is ignored entirely.

    Reason: FunASR 1.4.1 corrupts the text inside ``sentence_info``. On the bundled
    70s sample it diverges from the top-level text at character 297, scrambling
    punctuation placement — top level reads ``要聊一天，但是我觉得...足够。好，谢谢。``
    while ``sentence_info`` reads ``要聊一天但，是我觉得...足够好谢谢好非。``. From that
    point on its per-sentence timestamp arrays no longer line up with its own text
    (5 of 36 sentences mismatched), so any timing derived from it drifts by up to
    1.7 seconds.

    The top-level pair, by contrast, satisfies an exact invariant that this
    function asserts: ``len(split_tokens(text)) == len(timestamp)``. Verified at
    333/333 on the 70s sample and 14/14 on the 4.5s one.

    Kept separate from model execution so it can be tested against canned data
    without downloading a model.
    """
    if not res:
        return AsrOutput()

    item = res[0]
    text = (item.get("text") or "").strip()
    timestamps = item.get("timestamp") or []
    if not text:
        return AsrOutput()

    pairs = split_tokens(text)
    degraded = 0

    if len(pairs) == len(timestamps):
        tokens = [
            AsrToken(
                text=tok,
                start=float(ts[0]) / 1000.0,
                end=float(ts[1]) / 1000.0,
                punct_after=punct or None,
            )
            for (tok, punct), ts in zip(pairs, timestamps)
        ]
    else:
        # Never spread tokens evenly to paper over a mismatch. That produces a
        # timeline that looks plausible and is entirely fabricated: on a 35-minute
        # clip a 19-token discrepancy (0.2%) once turned into a uniform 259
        # tokens-per-minute smear whose onset error measured 28 SECONDS. A job that
        # fails is recoverable; a job that silently invents timing is not.
        raise ValueError(
            f"Số token ({len(pairs)}) không khớp số timestamp ({len(timestamps)}) "
            f"do FunASR trả về. Không dựng timeline từ dữ liệu lệch — lệch bao nhiêu "
            f"cũng có nghĩa là cách tách token không khớp với model. "
            f"Báo lại kèm đoạn text để sửa split_tokens."
        )

    return AsrOutput(sentences=_group_into_sentences(tokens, gap_sec), degraded_sentences=degraded)


class FunASRParaformer:
    """FunASR-backed implementation of :class:`~zhsub.asr.base.ASREngine`."""

    def __init__(
        self,
        model: str = "paraformer-zh",
        vad_model: str = "fsmn-vad",
        punc_model: str = "ct-punc",
        device: str = "cpu",
        batch_size_s: int = 300,
    ) -> None:
        self.name = "funasr-paraformer"
        self.models = {"asr": model, "vad": vad_model, "punc": punc_model}
        self.device = device
        self.batch_size_s = batch_size_s
        self._model = None
        self.version = _funasr_version()

    def _ensure_model(self):
        if self._model is None:
            from funasr import AutoModel

            log.info("Đang tải model FunASR (%s) lên %s...", self.models["asr"], self.device)
            self._model = AutoModel(
                model=self.models["asr"],
                vad_model=self.models["vad"],
                punc_model=self.models["punc"],
                device="cuda:0" if self.device == "cuda" else "cpu",
                disable_update=True,
                disable_pbar=True,
            )
        return self._model

    def transcribe(self, wav_path: str | Path) -> AsrOutput:
        model = self._ensure_model()
        # `sentence_timestamp=True` is deliberately NOT requested: it only populates
        # `sentence_info`, which this version corrupts (see parse_funasr_result).
        # The top-level text/timestamp pair is all we need and is trustworthy.
        res = model.generate(input=str(wav_path), batch_size_s=self.batch_size_s)
        return parse_funasr_result(res)


def _funasr_version() -> str:
    try:
        from importlib.metadata import version

        return version("funasr")
    except Exception:  # pragma: no cover
        return "unknown"
