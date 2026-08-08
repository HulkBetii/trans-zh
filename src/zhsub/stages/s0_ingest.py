"""S0 — ingest: nguồn bất kỳ -> WAV 16kHz mono + ``ingest.json``."""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config
from ..jsonio import write_doc
from ..media import MediaError, probe_duration, to_wav
from ..models import IngestDoc, MediaInfo, SourceInfo

_URL_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
_WIN_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def is_url(source: str) -> bool:
    return bool(_URL_RE.match(source))


def make_job_id(source: str) -> str:
    """Job id **tất định** theo nguồn.

    Chạy lại cùng một file thì rơi vào đúng job cũ và resume được, thay vì đẻ ra
    thư mục work mới rồi làm lại từ đầu. Muốn chạy sạch thì xoá ``work/<job_id>/``.
    """
    key = source if is_url(source) else str(Path(source).resolve()).lower()
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:8]
    stem = source.rsplit("/", 1)[-1] if is_url(source) else Path(source).stem
    slug = _WIN_UNSAFE.sub("_", stem).strip(". ")[:40] or "job"
    return f"{slug}_{digest}"


def _download(source: str, dest_dir: Path, cookies_from_browser: str) -> Path:
    """Tải audio bằng yt-dlp.

    ``--cookies-from-browser`` đọc cookie có sẵn trong browser của người dùng;
    pipeline không bao giờ tự nhập tài khoản hay mật khẩu.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(dest_dir / "source.%(ext)s")
    cmd = [
        sys.executable, "-m", "yt_dlp",
        "-f", "bestaudio/best",
        "--no-playlist",
        "--no-progress",
        "-o", out_tmpl,
        source,
    ]
    if cookies_from_browser:
        cmd[-1:-1] = ["--cookies-from-browser", cookies_from_browser]

    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-15:]
        raise MediaError(
            f"yt-dlp lỗi (exit {proc.returncode}):\n" + "\n".join(tail)
            + "\n\nVideo cần đăng nhập thì đặt ingest.cookies_from_browser trong zhsub.toml "
            "(chrome / edge / firefox...) và đăng nhập sẵn trên browser đó."
        )

    files = [p for p in dest_dir.glob("source.*") if p.suffix != ".json"]
    if not files:
        raise MediaError(f"yt-dlp chạy xong nhưng không thấy file nào trong {dest_dir}")
    return max(files, key=lambda p: p.stat().st_size)


def run(source: str, work_dir: Path, cfg: Config, job_id: str | None = None) -> IngestDoc:
    """Chuẩn hoá ``source`` về WAV và ghi ``ingest.json``.

    Bỏ qua nếu đã có ``ingest.json`` + WAV hợp lệ, để resume không tải lại video.
    """
    job_id = job_id or make_job_id(source)
    work_dir.mkdir(parents=True, exist_ok=True)
    wav_path = work_dir / "audio.wav"
    out_json = work_dir / "ingest.json"

    if out_json.is_file() and wav_path.is_file() and wav_path.stat().st_size > 44:
        from ..jsonio import read_doc

        return read_doc(out_json, IngestDoc)

    if is_url(source):
        kind: str = "url"
        media_path = _download(source, work_dir / "download", cfg.ingest.cookies_from_browser)
        sha = None
    else:
        kind = "local"
        media_path = Path(source).resolve()
        if not media_path.is_file():
            raise FileNotFoundError(f"Không tìm thấy file: {media_path}")
        sha = _sha256(media_path)

    to_wav(media_path, wav_path, cfg.ingest.sample_rate, cfg.ingest.channels)

    doc = IngestDoc(
        job_id=job_id,
        source=SourceInfo(kind=kind, uri=source, sha256=sha, title=media_path.stem),
        media=MediaInfo(
            wav_path=wav_path.name,
            duration_sec=round(probe_duration(wav_path), 3),
            sample_rate=cfg.ingest.sample_rate,
            channels=cfg.ingest.channels,
        ),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )
    write_doc(out_json, doc)
    return doc


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk):
            h.update(block)
    return h.hexdigest()
