"""S1 — ASR: WAV -> ``asr.json`` with token-level timestamps.

VAD segmentation is left to FunASR by default (``AutoModel`` + ``vad_model``
already returns absolute timestamps). The outer chunk layer only kicks in for
files longer than the configured threshold — every extra offset layer is one more
place the arithmetic can go wrong.
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
    """Convert an :class:`AsrOutput` into an :class:`AsrDoc`. Pure, hence testable.

    Tokens get a single continuous global index; ``raw_segments`` only point into
    that range rather than keeping their own copy of the text — one source of truth.
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
    """Merge overlapping or touching intervals.

    ``vad_speech`` is derived from sentence boundaries rather than a second
    dedicated VAD pass: running VAD again costs nearly as much as the ASR itself,
    while both consumers (chunking in S2, extending cues into silence in S5) only
    need to know *where speech is*, for which sentence granularity is enough.
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
    """Transcribe a whole file, chunking when needed. Timestamps are always absolute."""
    windows = plan_chunks(
        duration_sec,
        cfg.asr.outer_chunk_threshold_sec,
        cfg.asr.outer_chunk_sec,
        cfg.asr.outer_chunk_overlap_sec,
    )
    if len(windows) == 1:
        return engine.transcribe(wav_path)

    log.info("Audio dài %.1f phút -> chia %d chunk", duration_sec / 60, len(windows))
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
    doc = build_asr_doc(out, engine, ingest.media.duration_sec)
    write_doc(out_json, doc)
    log.info("S1 xong: %d token, %d câu thô", len(doc.tokens), len(doc.raw_segments))
    return doc
