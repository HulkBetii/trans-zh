"""Harness benchmark: so paraformer-zh với faster-whisper large-v3.

    uv run python -m bench.run --media bench/data/clip.mp4 --ref bench/data/ref.srt

In bảng markdown ra stdout.
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
    onset: OnsetResult
    rtf: float
    load_sec: float
    note: str = ""


def _run_paraformer(wav: Path, cfg: Config) -> tuple[list[tuple[str, float, float]], str, float, float]:
    from zhsub.asr.funasr_paraformer import FunASRParaformer

    engine = FunASRParaformer(
        model=cfg.asr.model,
        vad_model=cfg.asr.vad_model,
        punc_model=cfg.asr.punc_model,
        device=cfg.asr.resolve_device(),
        batch_size_s=cfg.asr.batch_size_s,
    )
    t0 = time.perf_counter()
    engine._ensure_model()  # tải model trước để không tính vào RTF
    load_sec = time.perf_counter() - t0

    t1 = time.perf_counter()
    out = engine.transcribe(wav)
    elapsed = time.perf_counter() - t1

    tokens = [(t.text, t.start, t.end) for s in out.sentences for t in s.tokens]
    full_text = "".join(s.text for s in out.sentences)
    return tokens, full_text, elapsed, load_sec


def _fmt(value: float, digits: int = 1) -> str:
    return "n/a" if value != value else f"{value:.{digits}f}"


def _table(rows: list[Row]) -> str:
    head = (
        "| Engine | CER | Onset median | Onset p90 | ≤200ms | ≤500ms | Coverage | RTF |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|"
    )
    lines = [head]
    for r in rows:
        o = r.onset
        lines.append(
            f"| {r.engine} | {_fmt(r.cer * 100, 2)}% | {_fmt(o.median_ms)} ms | "
            f"{_fmt(o.p90_ms)} ms | {_fmt(o.within_200ms)}% | {_fmt(o.within_500ms)}% | "
            f"{_fmt(o.coverage)}% ({o.n_matched}/{o.n_ref}) | {_fmt(r.rtf, 3)} |"
        )
    return "\n".join(lines)


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
        except Exception as exc:  # noqa: BLE001 - báo lỗi rồi chạy tiếp engine khác
            print(f"  LỖI: {type(exc).__name__}: {exc}")
            continue

        chars, times = expand_tokens_to_chars(tokens)
        rows.append(
            Row(
                engine=label,
                cer=compute_cer(ref_cues, text),
                onset=compute_onset(ref_cues, chars, times),
                rtf=elapsed / duration if duration else float("nan"),
                load_sec=load_sec,
            )
        )
        print(f"  {len(tokens)} token, {elapsed:.1f}s (load {load_sec:.1f}s)")

    if not rows:
        raise SystemExit("Không engine nào chạy được.")

    print("\n" + "=" * 72)
    print(f"\n**Audio**: `{args.media}` — {duration / 60:.1f} phút  ")
    print(f"**Reference**: `{args.ref}` — {len(ref_cues)} cue  ")
    print(f"**Device**: {device}\n")
    print(_table(rows))
    print(
        "\n*CER đo sau khi chuẩn hoá hai phía giống nhau (phồn→giản, bỏ dấu câu, "
        "chữ số Ả Rập→Hán). Onset đo ở cấp ký tự qua alignment, không so mốc bắt "
        "đầu của segment. Coverage là tỉ lệ cue tham chiếu có ký tự đầu khớp được; "
        "coverage thấp thì hai cột median/p90 kém đại diện. RTF = thời gian xử lý "
        "/ độ dài audio, không tính thời gian nạp model.*"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
