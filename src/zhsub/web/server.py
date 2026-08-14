"""Giao diện web chạy local cho zhsub.

Chỉ là một lớp mỏng trên ``zhsub.api`` — mọi thứ nó cần đã có sẵn ở đó từ trước:
``translate_video`` nhận ``on_progress`` và ``cancel``, còn ``job_health`` trả về
đúng những con số cần liếc sau mỗi lần chạy.

Nhận ĐƯỜNG DẪN file chứ không nhận upload: video ở đây thường 1-2 GB, chép qua
HTTP chỉ để rồi ffmpeg đọc từ đĩa là vô nghĩa.
"""

from __future__ import annotations

import json
import queue
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel

from ..api import job_health, translate_video
from ..config import Config
from ..jobs import JobStore
from ..models import RenderReport
from ..progress import Cancelled, Progress
from ..stages import s0_ingest

app = FastAPI(title="zhsub")
_PAGE = Path(__file__).with_name("index.html")


@dataclass
class _Run:
    """Một lần chạy đang diễn ra. Sống trong bộ nhớ, mất khi tắt server."""

    job_id: str
    cancel: threading.Event
    events: queue.Queue = field(default_factory=queue.Queue)
    finished: bool = False
    error: str = ""


_runs: dict[str, _Run] = {}
_runs_lock = threading.Lock()


class StartRequest(BaseModel):
    source: str
    target: str = "vi"


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return _PAGE.read_text(encoding="utf-8")


def _outputs_for(work_dir: Path) -> list[str]:
    """File đầu ra của một job.

    Phụ đề lấy từ ``render_report.json`` vì đó là nguồn có thẩm quyền — S5 đặt tên
    theo phần gốc của nguồn (``video-6.vi.srt``) chứ không theo job_id. Còn file
    lồng tiếng thì S6 đặt theo job_id (``video-6_e0e4a896.vi.mp3``). Hai quy ước
    khác nhau, nên đoán bằng cách so chuỗi là sai — hỏi thẳng từng bên.
    """
    names: list[str] = []
    report_path = work_dir / "render_report.json"
    if report_path.is_file():
        report = RenderReport.model_validate(json.loads(report_path.read_text(encoding="utf-8")))
        names += [Path(o).name for o in report.outputs]
    names += [p.name for p in Path("output").glob(f"{work_dir.name}.*.mp3")]
    return [n for n in dict.fromkeys(names) if (Path("output") / n).is_file()]


@app.get("/api/jobs")
def list_jobs() -> list[dict]:
    cfg = Config.load()
    out = []
    for job in JobStore(cfg.paths.jobs_db).list():
        work_dir = Path(cfg.paths.work_dir) / job.job_id
        running = job.job_id in _runs and not _runs[job.job_id].finished
        out.append(
            {
                "job_id": job.job_id,
                "status": "running" if running else job.status,
                "stage": job.stage or "",
                "error": job.error or "",
                "health": asdict(job_health(work_dir)) if work_dir.is_dir() else None,
                "outputs": _outputs_for(work_dir) if work_dir.is_dir() else [],
            }
        )
    return out


@app.post("/api/jobs")
def start_job(req: StartRequest) -> dict:
    source = Path(req.source.strip('"'))
    if not source.is_file():
        raise HTTPException(400, f"Không thấy file: {source}")

    job_id = s0_ingest.make_job_id(str(source))
    with _runs_lock:
        if job_id in _runs and not _runs[job_id].finished:
            raise HTTPException(409, f"Job {job_id} đang chạy")
        run = _Run(job_id=job_id, cancel=threading.Event())
        _runs[job_id] = run

    def worker() -> None:
        try:
            translate_video(
                source,
                targets=[req.target],
                on_progress=run.events.put,
                cancel=run.cancel,
            )
        except Cancelled:
            run.error = "đã huỷ"
        except Exception as exc:  # noqa: BLE001 - hiện lên giao diện thay vì traceback
            run.error = f"{type(exc).__name__}: {exc}"
        finally:
            run.finished = True
            run.events.put(None)  # đánh dấu hết luồng cho SSE

    threading.Thread(target=worker, daemon=True, name=f"zhsub-{job_id[:12]}").start()
    return {"job_id": job_id}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    run = _runs.get(job_id)
    if run is None or run.finished:
        raise HTTPException(404, "Job không chạy")
    # Huỷ ở ranh giới stage, nên mọi thứ đã ghi xong vẫn giữ nguyên và `resume`
    # chạy tiếp được từ đó.
    run.cancel.set()
    return {"ok": True}


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str) -> StreamingResponse:
    run = _runs.get(job_id)
    if run is None:
        raise HTTPException(404, "Job không chạy")

    def stream():
        while True:
            item = run.events.get()
            if item is None:
                payload = {"done": True, "error": run.error}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return
            p: Progress = item
            payload = {
                "stage": p.stage,
                "label": p.stage_label,
                "fraction": round(p.fraction, 4),
                "message": p.message,
            }
            yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/output/{name}")
def download(name: str) -> FileResponse:
    # Chặn đi ngược thư mục: chỉ phục vụ file nằm thẳng trong output/.
    path = Path("output") / Path(name).name
    if not path.is_file():
        raise HTTPException(404, f"Không thấy {name}")
    return FileResponse(path, filename=path.name)


def serve(host: str = "127.0.0.1", port: int = 8756) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="warning")
