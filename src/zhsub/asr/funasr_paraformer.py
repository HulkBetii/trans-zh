"""ASR bằng FunASR: paraformer-zh + fsmn-vad + ct-punc.

Timestamp sinh ra ở đây là **nguồn sự thật duy nhất** của cả pipeline. Không có
stage nào phía sau được phép tạo ra hay sửa mốc thời gian ngoài việc kéo dài vào
khoảng lặng đã biết.

Điểm tinh tế nhất là ghép mảng ``timestamp`` với text: ``ct-punc`` chèn dấu câu
vào text nhưng dấu câu **không có** timestamp riêng, nên hai mảng lệch độ dài nếu
cứ zip thẳng. Vì vậy text được tách token theo đúng quy ước của FunASR (mỗi ký tự
Hán là một token, mỗi từ Latin liền mạch là một token, dấu câu treo vào token
liền trước) rồi mới đối chiếu số lượng.
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
    return ch.isascii() and (ch.isalnum() or ch == "'")


def split_tokens(text: str) -> list[list[str]]:
    """Tách text đã có dấu câu thành ``[[token, dấu_câu_theo_sau], ...]``.

    Quy ước bám theo cách FunASR sinh mảng ``timestamp``: một ký tự Hán = một
    token, một từ Latin liền mạch = một token, dấu câu không phải token.
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
        elif _is_punct(ch):
            flush()
            if tokens:
                tokens[-1][1] += ch
            # dấu câu đứng đầu câu, không có token nào trước đó -> bỏ
        elif _is_ascii_word(ch):
            latin += ch
        else:
            flush()
            tokens.append([ch, ""])
    flush()
    return tokens


def _build_sentence(text: str, timestamps: list, start_ms: float, end_ms: float) -> tuple[AsrSentence, bool]:
    """Ghép text + mảng timestamp thành một :class:`AsrSentence`.

    Returns:
        ``(sentence, degraded)`` — ``degraded=True`` nghĩa là số timestamp không
        khớp số token nên thời gian được chia đều, timestamp của câu đó kém tin cậy.
    """
    pairs = split_tokens(text)
    degraded = False

    if timestamps and len(timestamps) == len(pairs):
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
        degraded = True
        if timestamps:
            log.warning(
                "Số timestamp (%d) không khớp số token (%d) ở câu %r — chia đều thời gian",
                len(timestamps), len(pairs), text[:30],
            )
        s = float(start_ms) / 1000.0
        e = float(end_ms) / 1000.0
        n = max(len(pairs), 1)
        step = (e - s) / n if e > s else 0.0
        tokens = [
            AsrToken(
                text=tok,
                start=s + i * step,
                end=s + (i + 1) * step,
                punct_after=punct or None,
            )
            for i, (tok, punct) in enumerate(pairs)
        ]

    if tokens:
        sent_start, sent_end = tokens[0].start, tokens[-1].end
    else:
        sent_start, sent_end = float(start_ms) / 1000.0, float(end_ms) / 1000.0

    return AsrSentence(text=text, start=sent_start, end=sent_end, tokens=tokens), degraded


def parse_funasr_result(res: list[dict]) -> AsrOutput:
    """Chuyển output thô của ``AutoModel.generate`` sang :class:`AsrOutput`.

    Tách riêng khỏi phần chạy model để test được bằng dữ liệu đóng hộp, không cần
    tải model thật.
    """
    if not res:
        return AsrOutput()

    item = res[0]
    sentences: list[AsrSentence] = []
    degraded = 0

    info = item.get("sentence_info")
    if info:
        for s in info:
            text = (s.get("text") or "").strip()
            if not text:
                continue
            ts = s.get("timestamp") or []
            start = s.get("start", ts[0][0] if ts else 0)
            end = s.get("end", ts[-1][1] if ts else 0)
            sent, deg = _build_sentence(text, ts, start, end)
            if sent.tokens:
                sentences.append(sent)
                degraded += int(deg)
    else:
        # Không có punc_model (hoặc bản FunASR cũ): cả file là một câu.
        text = (item.get("text") or "").strip()
        ts = item.get("timestamp") or []
        if text:
            start = ts[0][0] if ts else 0
            end = ts[-1][1] if ts else 0
            sent, deg = _build_sentence(text, ts, start, end)
            if sent.tokens:
                sentences.append(sent)
                degraded += int(deg)

    return AsrOutput(sentences=sentences, degraded_sentences=degraded)


class FunASRParaformer:
    """Implementation :class:`~zhsub.asr.base.ASREngine` bằng FunASR."""

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
        res = model.generate(input=str(wav_path), batch_size_s=self.batch_size_s)
        return parse_funasr_result(res)


def _funasr_version() -> str:
    try:
        from importlib.metadata import version

        return version("funasr")
    except Exception:  # pragma: no cover
        return "unknown"
