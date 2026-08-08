"""zhsub command line interface."""

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

MEDIA_SUFFIXES = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".ts",
    ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus",
}


@app.callback()
def _root() -> None:
    """No-op root callback.

    Without it, Typer collapses a single-command app into a bare CLI and `zhsub
    asr file.mp4` parses "asr" as the source argument.
    """


def _setup_logging(verbose: bool) -> None:
    from rich.logging import RichHandler

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(message)s",
        datefmt="%H:%M:%S",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )
    # FunASR and modelscope are extremely chatty at INFO level.
    for noisy in ("modelscope", "funasr", "httpx", "urllib3", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _parse_targets(value: str) -> list[str]:
    langs = [t.strip() for t in value.split(",") if t.strip()]
    unknown = [t for t in langs if t not in ("vi", "en")]
    if unknown:
        raise typer.BadParameter(f"Ngôn ngữ chưa hỗ trợ: {unknown}. Chỉ có 'vi' và 'en'.")
    if not langs:
        raise typer.BadParameter("Phải chỉ định ít nhất một ngôn ngữ đích.")
    return langs


@app.command("asr")
def cmd_asr(
    source: str = typer.Argument(..., help="File video/audio, hoặc URL (Bilibili...)"),
    out: Path = typer.Option(..., "--out", "-o", help="File .srt để ghi transcript thô"),
    config: Path | None = typer.Option(None, "--config", "-c", help="Đường dẫn zhsub.toml"),
    device: str | None = typer.Option(None, "--device", help="auto | cuda | cpu"),
    force: bool = typer.Option(False, "--force", help="Chạy lại ASR kể cả khi đã có asr.json"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Chỉ chạy ASR và xuất SRT tiếng Trung thô, để kiểm tra timeline."""
    _setup_logging(verbose)
    from .stages import s0_ingest, s1_asr

    cfg = Config.load(config)
    if device:
        cfg.asr.device = device  # type: ignore[assignment]

    job_id = s0_ingest.make_job_id(source)
    work_dir = Path(cfg.paths.work_dir) / job_id
    typer.echo(f"job_id : {job_id}")

    ingest = s0_ingest.run(source, work_dir, cfg, job_id=job_id)
    typer.echo(f"audio  : {ingest.media.duration_sec / 60:.1f} phút")

    doc = s1_asr.run(work_dir, cfg, force=force)
    cues = [
        Cue(start=seg.start, end=seg.end, text=doc.display_text(*seg.token_range))
        for seg in doc.raw_segments
    ]
    write_srt(cues, out)
    typer.echo(f"token  : {len(doc.tokens)}")
    typer.echo(f"cue    : {len(cues)} -> {out}")


@app.command("run")
def cmd_run(
    source: str = typer.Argument(..., help="File video/audio, hoặc URL"),
    target: str = typer.Option("vi", "--target", "-t", help="Ngôn ngữ đích: vi, en, hoặc vi,en"),
    out: Path = typer.Option(Path("./output"), "--out", "-o", help="Thư mục xuất phụ đề"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    bilingual: bool = typer.Option(False, "--bilingual", help="Dòng trên tiếng Trung, dòng dưới bản dịch"),
    device: str | None = typer.Option(None, "--device", help="auto | cuda | cpu"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Chạy trọn pipeline S0-S5 cho một file."""
    _setup_logging(verbose)
    from .jobs import JobStore
    from .pipeline import RunRequest, run_job

    cfg = Config.load(config)
    if device:
        cfg.asr.device = device  # type: ignore[assignment]

    store = JobStore(cfg.paths.jobs_db)
    req = RunRequest(source=source, targets=_parse_targets(target), out_dir=out, bilingual=bilingual)
    work_dir = run_job(req, cfg, store)
    typer.echo(f"xong -> {out}  (work: {work_dir})")


@app.command("resume")
def cmd_resume(
    job_id: str = typer.Argument(..., help="job_id cần chạy lại"),
    from_stage: str = typer.Option(..., "--from", help="ingest|asr|segment|glossary|translate|render"),
    out: Path = typer.Option(Path("./output"), "--out", "-o"),
    config: Path | None = typer.Option(None, "--config", "-c"),
    bilingual: bool = typer.Option(False, "--bilingual"),
    force: bool = typer.Option(False, "--force", help="Chạy lại stage bắt đầu kể cả khi đã có output"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Chạy lại một job từ một stage cụ thể, ví dụ sau khi sửa glossary.json."""
    _setup_logging(verbose)
    from .jobs import JobStore
    from .pipeline import RunRequest, run_job

    cfg = Config.load(config)
    store = JobStore(cfg.paths.jobs_db)
    job = store.get(job_id)
    if job is None:
        raise typer.BadParameter(f"Không tìm thấy job {job_id!r} trong {cfg.paths.jobs_db}")

    req = RunRequest(
        source=job.source_uri,
        targets=job.targets or ["vi"],
        out_dir=out,
        bilingual=bilingual,
        from_stage=from_stage,
        force=force,
    )
    run_job(req, cfg, store, job_id=job_id)
    typer.echo(f"xong -> {out}")


@app.command("batch")
def cmd_batch(
    folder: Path = typer.Argument(..., help="Thư mục chứa video/audio"),
    target: str = typer.Option("vi", "--target", "-t"),
    out: Path = typer.Option(Path("./output"), "--out", "-o"),
    concurrency: int = typer.Option(1, "--concurrency", "-j", min=1, max=8),
    config: Path | None = typer.Option(None, "--config", "-c"),
    bilingual: bool = typer.Option(False, "--bilingual"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Chạy cả thư mục. Resume được sau khi crash hoặc mất điện."""
    _setup_logging(verbose)
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from .jobs import JobStore
    from .pipeline import RunRequest, run_job
    from .stages import s0_ingest

    cfg = Config.load(config)
    targets = _parse_targets(target)
    store = JobStore(cfg.paths.jobs_db)

    reclaimed = store.reclaim_stale()
    if reclaimed:
        typer.echo(f"Thu hồi {len(reclaimed)} job kẹt ở trạng thái running: {reclaimed}")

    sources = sorted(
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES
    )
    if not sources:
        raise typer.BadParameter(f"Không thấy file media nào trong {folder}")

    todo: list[Path] = []
    for path in sources:
        job = store.get(s0_ingest.make_job_id(str(path)))
        if job is not None and job.status == "done":
            continue
        todo.append(path)

    typer.echo(f"{len(sources)} file, {len(todo)} cần xử lý, concurrency {concurrency}")
    if not todo:
        return

    failures: list[tuple[str, str]] = []

    def _one(path: Path) -> None:
        req = RunRequest(
            source=str(path), targets=targets, out_dir=out, bilingual=bilingual
        )
        run_job(req, cfg, store)

    # Threads, not processes: each job is dominated by GPU inference and network
    # waits that release the GIL, and a shared process keeps one ASR model in VRAM
    # instead of one per worker.
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        futures = {pool.submit(_one, p): p for p in todo}
        for fut in as_completed(futures):
            path = futures[fut]
            try:
                fut.result()
                typer.echo(f"  OK   {path.name}")
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the batch
                failures.append((path.name, f"{type(exc).__name__}: {exc}"))
                typer.echo(f"  LỖI  {path.name}: {type(exc).__name__}: {exc}")

    typer.echo(f"\nHoàn tất: {len(todo) - len(failures)}/{len(todo)} thành công")
    if failures:
        typer.echo("Các file lỗi (chạy `zhsub batch` lại để thử tiếp):")
        for name, err in failures:
            typer.echo(f"  {name}: {err}")
        raise typer.Exit(code=1)


@app.command("jobs")
def cmd_jobs(
    config: Path | None = typer.Option(None, "--config", "-c"),
    status: str | None = typer.Option(None, "--status", help="pending|running|done|failed"),
) -> None:
    """Liệt kê job trong jobs.db."""
    from .jobs import JobStore

    cfg = Config.load(config)
    jobs = JobStore(cfg.paths.jobs_db).list(status)
    if not jobs:
        typer.echo("(chưa có job nào)")
        return
    for job in jobs:
        line = f"{job.status:8} {job.stage or '-':10} {job.job_id}"
        if job.error:
            line += f"  | {job.error[:80]}"
        typer.echo(line)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
