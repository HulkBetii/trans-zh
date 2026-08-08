"""S5 — render .srt and .ass.

Cardinality is fixed here: **one segment produces exactly one cue**, which is what
acceptance criterion #2 requires (vi line count == en line count == segment count).
A translation too long for two lines therefore overflows and is warned about rather
than split or trimmed — timeline integrity outranks line length.
"""

from __future__ import annotations

import logging
from pathlib import Path

from ..config import Config
from ..jsonio import read_doc, write_doc
from ..models import (
    RenderReport,
    RenderWarning,
    Segment,
    SegmentsDoc,
    TranslationsDoc,
)
from ..subtitle import Cue, write_srt
from ..timing import Span, cps, relieve_cps, wrap_lines

log = logging.getLogger(__name__)


def _relieve_and_wrap(
    segments: list[Segment],
    texts: dict[int, str],
    lang: str,
    cfg: Config,
    audio_duration_sec: float,
    warnings: list[RenderWarning],
) -> tuple[list[Span], dict[int, list[str]]]:
    """Give over-dense cues more time where silence allows, then wrap into lines."""
    limits = cfg.render.for_lang(lang)
    spans = [Span(s.start, s.end) for s in segments]

    for i, seg in enumerate(segments):
        text = texts.get(seg.id, "")
        if not text:
            continue
        rate = cps(text, spans[i].duration)
        if rate > limits.max_cps:
            new_end = relieve_cps(spans, i, text, limits.max_cps, audio_duration_sec)
            spans[i].end = max(spans[i].end, new_end)
            final_rate = cps(text, spans[i].duration)
            if final_rate > limits.max_cps:
                # Out of silence to borrow. Warn and move on: shortening the
                # translation is explicitly out of scope.
                warnings.append(
                    RenderWarning(
                        segment_id=seg.id, lang=lang, kind="cps_over",
                        detail=f"{text[:40]!r} còn {final_rate:.1f} CPS sau khi đã kéo dài",
                        value=round(final_rate, 2), limit=limits.max_cps,
                    )
                )

    wrapped: dict[int, list[str]] = {}
    for seg in segments:
        text = texts.get(seg.id, "")
        lines = wrap_lines(text, limits.max_chars_per_line, cfg.render.max_lines)
        if len(lines) > cfg.render.max_lines:
            warnings.append(
                RenderWarning(
                    segment_id=seg.id, lang=lang, kind="too_many_lines",
                    detail=f"{text[:40]!r} cần {len(lines)} dòng",
                    value=float(len(lines)), limit=float(cfg.render.max_lines),
                )
            )
        for line in lines:
            if len(line) > limits.max_chars_per_line:
                warnings.append(
                    RenderWarning(
                        segment_id=seg.id, lang=lang, kind="line_over",
                        detail=f"dòng dài {len(line)} ký tự: {line[:40]!r}",
                        value=float(len(line)), limit=float(limits.max_chars_per_line),
                    )
                )
        wrapped[seg.id] = lines
    return spans, wrapped


def build_cues(
    segments: list[Segment],
    texts: dict[int, str],
    lang: str,
    cfg: Config,
    audio_duration_sec: float,
    warnings: list[RenderWarning],
    bilingual: bool = False,
) -> list[Cue]:
    spans, wrapped = _relieve_and_wrap(
        segments, texts, lang, cfg, audio_duration_sec, warnings
    )

    cues: list[Cue] = []
    for seg, span in zip(segments, spans):
        body = "\n".join(wrapped[seg.id])
        if bilingual:
            zh_limits = cfg.render.for_lang("zh")
            zh_lines = wrap_lines(seg.text_zh, zh_limits.max_chars_per_line, cfg.render.max_lines)
            body = "\n".join(zh_lines) + "\n" + body
        cues.append(Cue(start=span.start, end=span.end, text=body))
    return cues


def run(
    work_dir,
    cfg: Config,
    langs: list[str],
    out_dir,
    bilingual: bool = False,
    formats: tuple[str, ...] = ("srt", "ass"),
) -> RenderReport:
    work_dir, out_dir = Path(work_dir), Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    segments_doc = read_doc(work_dir / "segments.json", SegmentsDoc)
    segments = segments_doc.segments

    from ..models import IngestDoc

    ingest = read_doc(work_dir / "ingest.json", IngestDoc)
    duration = ingest.media.duration_sec
    stem = ingest.source.title or work_dir.name

    warnings: list[RenderWarning] = []
    outputs: list[str] = []

    for lang in langs:
        tdoc = read_doc(work_dir / f"translations.{lang}.json", TranslationsDoc)
        texts = {item.id: item.translation for item in tdoc.items}

        # Acceptance criterion #2 is enforced here, not assumed downstream.
        if len(tdoc.items) != len(segments):
            raise ValueError(
                f"translations.{lang}.json có {len(tdoc.items)} mục nhưng segments.json "
                f"có {len(segments)} segment. Quan hệ phải là 1-1."
            )

        cues = build_cues(segments, texts, lang, cfg, duration, warnings, bilingual)
        suffix = f".{lang}.bilingual" if bilingual else f".{lang}"

        if "srt" in formats:
            path = out_dir / f"{stem}{suffix}.srt"
            write_srt(cues, path)
            outputs.append(str(path))
        if "ass" in formats:
            path = out_dir / f"{stem}{suffix}.ass"
            _write_ass(cues, path)
            outputs.append(str(path))

    report = RenderReport(outputs=outputs, warnings=warnings)
    write_doc(work_dir / "render_report.json", report)

    if warnings:
        by_kind: dict[str, int] = {}
        for w in warnings:
            by_kind[w.kind] = by_kind.get(w.kind, 0) + 1
        log.warning("S5: %d cảnh báo trình bày: %s", len(warnings), by_kind)
    log.info("S5 xong: %d file", len(outputs))
    return report


def _write_ass(cues: list[Cue], path: Path) -> None:
    import pysubs2

    subs = pysubs2.SSAFile()
    style = subs.styles["Default"].copy()
    style.fontname = "Arial"
    style.fontsize = 20
    style.outline = 1.5
    style.shadow = 0.5
    subs.styles["Default"] = style

    for cue in cues:
        subs.append(
            pysubs2.SSAEvent(
                start=int(round(cue.start * 1000)),
                end=int(round(cue.end * 1000)),
                text=cue.text.replace("\n", r"\N"),
            )
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    subs.save(str(path), encoding="utf-8-sig")
