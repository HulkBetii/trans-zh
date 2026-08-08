"""S1 — ASR: WAV -> ``asr.json`` với timestamp cấp token.

Mặc định giao hết việc cắt VAD cho FunASR (``AutoModel`` + ``vad_model`` đã trả
timestamp tuyệt đối sẵn). Lớp chunk bên ngoài chỉ bật khi file dài hơn ngưỡng
trong config — mỗi tầng offset thêm vào là một chỗ có thể cộng sai.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ..asr.base import ASREngine, AsrOutput, merge_outputs, plan_chunks
from ..config import Config
from ..jsonio import read_doc, write_doc
from ..media import slice_wav
from ..models import AsrDoc, EngineInfo, IngestDoc, RawSegment, Token

log = logging.getLogger(__name__)


def build_engine(cfg: Config) -> ASREngine:
    if cfg.asr.engine != "funasr-paraformer":
        raise ValueError(
            f"Engine {cfg.asr.engine!r} chưa được implement. v1 chỉ có 'funasr-paraformer'."
        )
    from ..asr.funasr_paraformer import FunASRParaformer

    return FunASRParaformer(
        model=cfg.asr.model,
        vad_model=cfg.asr.vad_model,
        punc_model=cfg.asr.punc_model,
        device=cfg.asr.resolve_device(),
        batch_size_s=cfg.asr.batch_size_s,
    )


def build_asr_doc(out: AsrOutput, engine: ASREngine, duration_sec: float) -> AsrDoc:
    """Chuyển :class:`AsrOutput` sang :class:`AsrDoc` (hàm thuần, test được).

    Token được đánh index toàn cục liên tục; ``raw_segments`` chỉ trỏ vào dải
    index đó chứ không giữ bản sao text — một nguồn sự thật duy nhất.
    """
    tokens: list[Token] = []
    raw_segments: list[RawSegment] = []
    speech: list[tuple[float, float]] = []

    for sid, sent in enumerate(out.sentences):
        lo = len(tokens)
        for tok in sent.tokens:
            tokens.append(
                Token(
                    i=len(tokens),
                    text=tok.text,
                    start=round(tok.start, 3),
                    end=round(tok.end, 3),
                    punct_after=tok.punct_after,
                )
            )
        hi = len(tokens)
        if hi == lo:
            continue
        raw_segments.append(
            RawSegment(
                id=sid,
                start=tokens[lo].start,
                end=tokens[hi - 1].end,
                token_range=(lo, hi),
            )
        )
        speech.append((tokens[lo].start, tokens[hi - 1].end))

    return AsrDoc(
        engine=EngineInfo(
            name=engine.name,
            version=engine.version,
            models=dict(engine.models),
            device=engine.device,
        ),
        audio_duration_sec=round(duration_sec, 3),
        tokens=tokens,
        raw_segments=raw_segments,
        vad_speech=_merge_intervals(speech),
    )


def _merge_intervals(
    intervals: list[tuple[float, float]], gap: float = 0.0
) -> list[tuple[float, float]]:
    """Gộp các khoảng chồng nhau hoặc dính nhau.

    ``vad_speech`` ở đây suy ra từ mốc đầu/cuối của từng câu chứ không chạy lại
    riêng một lượt VAD: chạy VAD lần hai tốn gần bằng chạy ASR mà hai mục đích sử
    dụng (chia chunk ở S2, kéo dài phụ đề vào khoảng lặng ở S5) chỉ cần biết chỗ
    nào **có** tiếng nói, độ mịn cấp câu là đủ.
    """
    if not intervals:
        return []
    ordered = sorted(intervals)
    out = [list(ordered[0])]
    for s, e in ordered[1:]:
        if s <= out[-1][1] + gap:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [(round(s, 3), round(e, 3)) for s, e in out]


def transcribe_file(
    wav_path: Path,
    duration_sec: float,
    engine: ASREngine,
    cfg: Config,
    scratch_dir: Path | None = None,
) -> AsrOutput:
    """Nhận dạng cả file, tự chia chunk khi cần. Timestamp luôn tuyệt đối."""
    windows = plan_chunks(
        duration_sec,
        cfg.asr.outer_chunk_threshold_sec,
        cfg.asr.outer_chunk_sec,
        cfg.asr.outer_chunk_overlap_sec,
    )
    if len(windows) == 1:
        return engine.transcribe(wav_path)

    log.info("File dài %.1f phút -> chia %d chunk", duration_sec / 60, len(windows))
    scratch = scratch_dir or wav_path.parent / "chunks"
    scratch.mkdir(parents=True, exist_ok=True)
    parts: list[tuple[float, AsrOutput]] = []
    try:
        for idx, (start, end) in enumerate(windows):
            piece = scratch / f"chunk_{idx:04d}.wav"
            slice_wav(wav_path, piece, start, end - start)
            log.info("  chunk %d/%d  [%.1fs .. %.1fs]", idx + 1, len(windows), start, end)
            parts.append((start, engine.transcribe(piece)))
            piece.unlink(missing_ok=True)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)

    return merge_outputs(parts)


def run(work_dir: Path, cfg: Config, force: bool = False) -> AsrDoc:
    out_json = work_dir / "asr.json"
    if out_json.is_file() and not force:
        return read_doc(out_json, AsrDoc)

    ingest = read_doc(work_dir / "ingest.json", IngestDoc)
    wav_path = work_dir / ingest.media.wav_path

    engine = build_engine(cfg)
    out = transcribe_file(wav_path, ingest.media.duration_sec, engine, cfg)
    if out.degraded_sentences:
        log.warning(
            "%d câu có timestamp phải chia đều do lệch số token — timeline chỗ đó kém chính xác",
            out.degraded_sentences,
        )

    doc = build_asr_doc(out, engine, ingest.media.duration_sec)
    write_doc(out_json, doc)
    log.info("S1 xong: %d token, %d câu thô", len(doc.tokens), len(doc.raw_segments))
    return doc
