"""Benchmark harness: paraformer-zh versus faster-whisper large-v3.

    uv run python -m bench.run --media bench/data/clip.mp4 --ref bench/data/ref.srt

Prints a markdown table to stdout.
"""

from __future__ import annotations

import argparse
import time
from dataclasses import dataclass
from pathlib import Path

from zhsub.config import Config
from zhsub.media import probe_duration, to_wav
from zhsub.subtitle import read_subtitle

from .metrics import OnsetResult, compute_cer, compute_onset, expand_tokens_to_chars


@dataclass
class Row:
    engine: str
    cer: float
    onset_all: OnsetResult
    onset_paused: OnsetResult
    rtf: float
    load_sec: float
    note: str = ""


def _run_paraformer(
    wav: Path, cfg: Config
) -> tuple[list[tuple[str, float, float]], str, float, float]:
    from zhsub.asr.funasr_paraformer import FunASRParaformer

    engine = FunASRParaformer(
        model=cfg.asr.model,
        vad_model=cfg.asr.vad_model,
        punc_model=cfg.asr.punc_model,
        device=cfg.asr.resolve_device(),
        batch_size_s=cfg.asr.batch_size_s,
    )
    t0 = time.perf_counter()
    engine._ensure_model()  # load up front so it is excluded from RTF
    load_sec = time.perf_counter() - t0

    t1 = time.perf_counter()
    out = engine.transcribe(wav)
    elapsed = time.perf_counter() - t1

    tokens = [(t.text, t.start, t.end) for s in out.sentences for t in s.tokens]
    full_text = "".join(s.text for s in out.sentences)
    return tokens, full_text, elapsed, load_sec


def _fmt(value: float, digits: int = 1) -> str:
    return "n/a" if value != value else f"{value:.{digits}f}"


def _table(rows: list[Row], which: str) -> str:
    head = (
        "| Engine | CER | Onset median | Onset p90 | ≤200ms | ≤500ms | Coverage | RTF |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|"
    )
    lines = [head]
    for r in rows:
        o = r.onset_all if which == "all" else r.onset_paused
        lines.append(
            f"| {r.engine} | {_fmt(r.cer * 100, 2)}% | {_fmt(o.median_ms)} ms | "
            f"{_fmt(o.p90_ms)} ms | {_fmt(o.within_200ms)}% | {_fmt(o.within_500ms)}% | "
            f"{_fmt(o.coverage)}% ({o.n_matched}/{o.n_ref}) | {_fmt(r.rtf, 3)} |"
        )
    return "\n".join(lines)


def _describe_reference(ref_cues) -> str:
    """Report whether the reference looks hand-timed or auto-generated.

    A downloaded CC track measures agreement with another ASR system rather than
    ground truth, and its cue starts are mostly text-break points instead of speech
    onsets. That changes what every number below means, so it has to be stated.
    """
    from .metrics import cues_after_a_real_pause

    punct = "。，、；：？！…"
    n_punct = sum(1 for c in ref_cues if any(ch in punct for ch in c.text))
    gaps = [b.start - a.end for a, b in zip(ref_cues, ref_cues[1:])]
    contiguous = sum(1 for g in gaps if abs(g) < 1e-6)
    n_paused = sum(cues_after_a_real_pause(ref_cues))

    pct_punct = 100.0 * n_punct / max(len(ref_cues), 1)
    pct_contig = 100.0 * contiguous / max(len(gaps), 1)
    verdict = (
        "**CC máy sinh** (không dấu câu, mốc dính liền)"
        if pct_punct < 5 and pct_contig > 50
        else "có vẻ được căn tay"
    )
    return (
        f"**Reference**: {len(ref_cues)} cue — {verdict}  \n"
        f"  cue có dấu câu: {pct_punct:.1f}% · gap == 0: {pct_contig:.1f}% · "
        f"cue sau khoảng nghỉ thật: {n_paused}"
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--media", required=True, help="Video/audio tiếng Trung")
    ap.add_argument("--ref", required=True, help="SRT tham chiếu đã căn tay theo sóng âm")
    ap.add_argument("--engines", default="paraformer,whisper")
    ap.add_argument("--whisper-model", default="large-v3")
    ap.add_argument("--device", default=None, help="auto | cuda | cpu")
    ap.add_argument("--workdir", default="bench/_work")
    args = ap.parse_args()

    cfg = Config.load()
    if args.device:
        cfg.asr.device = args.device
    device = cfg.asr.resolve_device()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    wav = workdir / "bench.wav"
    if not wav.is_file():
        print(f"Đang chuẩn hoá {args.media} -> WAV 16kHz mono ...")
        to_wav(args.media, wav, cfg.ingest.sample_rate, cfg.ingest.channels)
    duration = probe_duration(wav)

    ref_cues = read_subtitle(args.ref)
    if not ref_cues:
        raise SystemExit(f"Reference {args.ref} không có cue nào đọc được")

    wanted = [e.strip() for e in args.engines.split(",") if e.strip()]
    rows: list[Row] = []

    for name in wanted:
        print(f"\n=== {name} ===")
        try:
            if name == "paraformer":
                tokens, text, elapsed, load_sec = _run_paraformer(wav, cfg)
                label = f"paraformer-zh ({device})"
            elif name == "whisper":
                from .whisper_runner import transcribe

                t0 = time.perf_counter()
                tokens, text, elapsed = transcribe(wav, args.whisper_model, device)
                load_sec = time.perf_counter() - t0 - elapsed
                label = f"faster-whisper {args.whisper_model} ({device})"
            else:
                print(f"  bỏ qua engine lạ: {name}")
                continue
        except Exception as exc:  # noqa: BLE001 - report and continue with other engines
            print(f"  LỖI: {type(exc).__name__}: {exc}")
            continue

        chars, times = expand_tokens_to_chars(tokens)
        rows.append(
            Row(
                engine=label,
                cer=compute_cer(ref_cues, text),
                onset_all=compute_onset(ref_cues, chars, times),
                onset_paused=compute_onset(ref_cues, chars, times, only_after_pause=True),
                rtf=elapsed / duration if duration else float("nan"),
                load_sec=load_sec,
            )
        )
        print(f"  {len(tokens)} token, {elapsed:.1f}s (load {load_sec:.1f}s)")

    if not rows:
        raise SystemExit("Không engine nào chạy được.")

    print("\n" + "=" * 72)
    print(f"\n**Audio**: `{args.media}` — {duration / 60:.1f} phút  ")
    print(_describe_reference(ref_cues))
    print(f"**Device**: {device}\n")

    print("### Bảng 1 — tất cả cue tham chiếu\n")
    print(_table(rows, "all"))
    print("\n### Bảng 2 — chỉ cue đứng sau khoảng nghỉ thật\n")
    print(_table(rows, "paused"))
    print(
        "\n*CER đo sau khi chuẩn hoá hai phía giống nhau (phồn→giản, bỏ dấu câu, "
        "chữ số Ả Rập→Hán). Onset đo ở cấp ký tự qua alignment, không so mốc bắt "
        "đầu của segment. Coverage là tỉ lệ cue tham chiếu có ký tự đầu khớp được; "
        "coverage thấp thì median/p90 kém đại diện. RTF = thời gian xử lý / độ dài "
        "audio, không tính thời gian nạp model.*\n"
        "\n*Bảng 2 mới là bảng đáng tin khi reference là CC máy sinh: ở đó mốc bắt "
        "đầu của cue tương ứng với lúc lời nói trở lại sau khoảng lặng, còn trong "
        "bảng 1 phần lớn mốc chỉ là chỗ công cụ CC ngắt text.*"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
