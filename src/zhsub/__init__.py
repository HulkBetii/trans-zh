"""zhsub — pipeline dịch phụ đề tiếng Trung sang tiếng Việt / tiếng Anh.

Dùng như thư viện (cho app desktop):

    import threading
    from zhsub import Config, Progress, translate_video

    cancel = threading.Event()          # bấm Cancel thì cancel.set()

    def on_progress(p: Progress) -> None:
        bar.value = p.fraction          # 0..1 cho cả job
        label.text = f"{p.stage_label} — {p.message}"

    result = translate_video(
        "video.mp4",
        targets=["vi", "en"],
        out_dir="./output",
        on_progress=on_progress,
        cancel=cancel,
    )
    print(result.outputs)               # danh sách file .srt/.ass đã ghi

Huỷ giữa chừng an toàn: mỗi stage ghi JSON của nó một lần duy nhất và ghi nguyên
tử, nên job bị huỷ chỉ mất đúng stage đang chạy dở. Gọi lại ``translate_video``
với cùng nguồn là chạy tiếp từ stage dở đó — riêng ở khâu dịch, cache được flush
sau từng batch nên phần đã trả tiền không mất.
"""

from .api import JobResult, translate_video
from .config import Config
from .progress import Cancelled, Progress, RunContext

__version__ = "0.1.0"

__all__ = [
    "Cancelled",
    "Config",
    "JobResult",
    "Progress",
    "RunContext",
    "translate_video",
    "__version__",
]
