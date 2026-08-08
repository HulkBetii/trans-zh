"""CLI của zhsub.

Giai đoạn 0 mới có lệnh ``asr``: đủ để sinh SRT thô làm nguyên liệu cho bước căn
tay reference của benchmark. Các lệnh ``run`` / ``batch`` / ``resume`` thuộc giai
đoạn build đầy đủ.
"""

from __future__ import annotations

import logging
from pathlib import Path

import typer

from .config import Config
from .subtitle import Cue, write_srt

app = typer.Typer(
    add_completion=False,
    help="Pipeline dịch phụ đề tiếng Trung sang tiếng Việt / tiếng Anh.",
    no_args_is_help=True,
)


def _setup_logging(verbose: bool) -> None:
    from rich.logging import RichHandler

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    # FunASR/modelscope log rất ồn ở mức INFO
    for noisy in ("modelscope", "funasr", "httpx", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@app.command("asr")
def cmd_asr(
    source: str = typer.Argument(..., help="File video/audio, hoặc URL (Bilibili...)"),
    out: Path = typer.Option(..., "--out", "-o", help="File .srt để ghi transcript thô"),
    config: Path | None = typer.Option(None, "--config", "-c", help="Đường dẫn zhsub.toml"),
    device: str | None = typer.Option(None, "--device", help="auto | cuda | cpu"),
    force: bool = typer.Option(False, "--force", help="Chạy lại ASR kể cả khi đã có asr.json"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Chỉ chạy ASR và xuất SRT tiếng Trung thô, để kiểm tra timeline.

    Đây cũng là lệnh dùng để sinh bản nháp cho việc căn tay file reference của
    benchmark: mở SRT này trong Subtitle Edit, sửa chữ **và kéo lại mốc thời gian
    theo sóng âm**, rồi lưu thành ref.srt.
    """
    _setup_logging(verbose)
    from .stages import s0_ingest, s1_asr

    cfg = Config.load(config)
    if device:
        cfg.asr.device = device  # type: ignore[assignment]

    job_id = s0_ingest.make_job_id(source)
    work_dir = Path(cfg.paths.work_dir) / job_id

    typer.echo(f"job_id : {job_id}")
    typer.echo(f"work   : {work_dir}")

    ingest = s0_ingest.run(source, work_dir, cfg, job_id=job_id)
    typer.echo(f"audio  : {ingest.media.duration_sec / 60:.1f} phút")

    doc = s1_asr.run(work_dir, cfg, force=force)

    cues = [
        Cue(
            start=seg.start,
            end=seg.end,
            text=doc.display_text(*seg.token_range),
        )
        for seg in doc.raw_segments
    ]
    write_srt(cues, out)

    typer.echo(f"device : {doc.engine.device}")
    typer.echo(f"token  : {len(doc.tokens)}")
    typer.echo(f"cue    : {len(cues)} -> {out}")


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
