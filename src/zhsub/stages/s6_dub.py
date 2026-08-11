"""S6 — sinh audio lồng tiếng từ bản dịch.

Không nằm trong ``zhsub run``: một cue là một lần gọi API trả tiền, video 17 phút
hết 184 lần. Chạy riêng bằng ``zhsub dub <job_id>`` khi bản dịch đã ưng.

Ba quyết định thiết kế, tất cả đều rút ra từ số đo trên một video thật:

* **Cắt lặng hai đầu mỗi đoạn.** Nhà cung cấp trả về ~0.22s lặng đầu và 0.27-0.46s
  lặng cuối trên mọi mẫu. Bỏ nó thu lại ~0.55 giây mỗi câu, và riêng việc đó đưa
  183/184 cue từ chật thành dư chỗ.
* **Chỉnh tốc độ chỉ cho phần dư.** Sau khi cắt lặng, cue chật nhất chỉ cần x1.11.
  Trước khi có bước cắt lặng, một cue cần tới x1.68 — quá trần 1.5 của API, mà
  dịch lại cũng không cứu được vì chữ đã ngắn hết mức ("Manh mối thứ nhất:").
* **Ghép theo mốc thời gian, không nối đuôi.** Tổng thời lượng đọc ngắn hơn
  timeline ~25%; nối đuôi thì tới cuối video tiếng chạy trước hình vài phút.

Tốc độ được tính TRƯỚC khi gọi API từ mô hình hiệu chuẩn, nên mỗi cue chỉ tốn một
lần gọi thay vì gọi rồi đo rồi gọi lại.
"""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config import Config
from ..dub import SpeechClient, assemble, decode_pcm, speech_bounds
from ..dub.text import normalize_for_speech
from ..jsonio import read_doc
from ..media import probe_duration
from ..models import SegmentsDoc, TranslationsDoc
from ..progress import RunContext, ensure_context

log = logging.getLogger(__name__)


def plan_speed(syllables: int, room_sec: float, cfg: Config) -> float:
    """Tốc độ đọc cho một cue, kẹp trong khoảng [base_speed, max_speed].

    Mặc định hai đầu bằng nhau nên mọi cue đọc cùng một tốc độ và không bao giờ bị
    đẩy nhanh — nghe thử bản có tăng tốc tới 1.15x thì rõ là gấp gáp. Câu dài hơn
    chỗ trống sẽ lấn sang khoảng lặng phía sau thay vì bị nén lại.

    ``room_sec`` là khoảng tới lúc cue kế tiếp bắt đầu, không phải độ dài cue: cue
    tràn 0.4 giây mà sau nó có 0.8 giây im lặng thì không lấn vào đâu cả. Đo trên
    một video thật, dùng đúng ràng buộc này thay vì độ dài cue đã kéo số cue chật
    từ 26% xuống 17%.
    """
    natural = cfg.dub.overhead_sec + syllables * cfg.dub.sec_per_syllable
    speed = cfg.dub.base_speed
    if room_sec > 0 and natural / speed > room_sec:
        speed = min(cfg.dub.max_speed, natural / room_sec)
    return max(speed, cfg.dub.base_speed)


def _rooms(segments: list, total_sec: float) -> list[float]:
    """Chỗ trống thật của từng cue: tới lúc cue sau bắt đầu."""
    starts = [s.start for s in segments]
    return [
        (starts[i + 1] if i + 1 < len(starts) else total_sec) - starts[i]
        for i in range(len(starts))
    ]


def run(
    work_dir, cfg: Config, lang: str, out_dir, force: bool = False,
    ctx: RunContext | None = None,
) -> Path:
    ctx = ensure_context(ctx)
    work_dir = Path(work_dir)
    segments = read_doc(work_dir / "segments.json", SegmentsDoc).segments
    items = {i.id: i for i in read_doc(work_dir / f"translations.{lang}.json", TranslationsDoc).items}

    voice_id = cfg.dub.require_voice()
    client = SpeechClient(cfg.dub.base_url, cfg.dub.api_key())
    clips_dir = work_dir / "dub" / lang
    clips_dir.mkdir(parents=True, exist_ok=True)

    total_sec = max(s.end for s in segments)
    rooms = _rooms(segments, total_sec)
    log.info("S6[%s]: %d cue, giọng %s, còn %d credit", lang, len(segments), voice_id, client.credits())

    def clip_path(seg, room: float) -> tuple[Path, str, float]:
        """Đường dẫn clip mang theo dấu vân tay của thứ sinh ra nó.

        Tên file chứa hash của (chữ đã chuẩn hoá + tốc độ + giọng), nên đổi bất kỳ
        thứ nào trong đó là clip cũ không còn được tìm thấy và cue được tổng hợp
        lại. Đặt tên theo mỗi id cue thì sửa luật chuẩn hoá xong chạy lại sẽ lặng lẽ
        dùng lại audio cũ — đúng loại bẫy mà prompt_version sinh ra ở khâu dịch.
        """
        text = normalize_for_speech(items[seg.id].translation)
        speed = plan_speed(len(text.split()), room, cfg)
        digest = hashlib.sha256(f"{text}|{speed:.2f}|{voice_id}".encode()).hexdigest()[:8]
        return clips_dir / f"{seg.id:05d}-{digest}.mp3", text, speed

    def fetch(job: tuple[int, object, float]) -> Path:
        """Tổng hợp một cue. Chạy trong luồng riêng, chỉ đụng tới file của chính nó."""
        _, seg, room = job
        clip, text, speed = clip_path(seg, room)
        # Đã có thì dùng lại: một lần chạy hỏng giữa chừng không phải trả tiền lại
        # cho những cue đã tổng hợp xong.
        if clip.is_file() and not force:
            return clip
        task_id = client.synthesize(text, voice_id, speed)
        url = client.wait(task_id).get("audio_url")
        if not url:
            raise RuntimeError(f"cue {seg.id}: task xong nhưng không có audio_url")
        client.download(url, clip)
        return clip

    jobs = [(i, seg, room) for i, (seg, room) in enumerate(zip(segments, rooms))]
    sped_up = sum(1 for _, seg, room in jobs if clip_path(seg, room)[2] > cfg.dub.base_speed)

    # Chạy song song vì mỗi cue gần như chỉ là chờ poll. Giải mã và ghép vẫn tuần tự
    # theo đúng thứ tự cue, nên kết quả không phụ thuộc luồng nào xong trước.
    done = 0
    try:
        with ThreadPoolExecutor(max_workers=max(1, cfg.dub.concurrency)) as pool:
            for _ in pool.map(fetch, jobs):
                done += 1
                ctx.report(done / max(len(jobs), 1), f"{lang}: {done}/{len(jobs)} cue")
    finally:
        client.close()

    clips: list[tuple[float, bytes]] = []
    for seg, room in zip(segments, rooms):
        clip = clip_path(seg, room)[0]
        begin, finish = speech_bounds(clip, probe_duration(clip))
        clips.append((seg.start, decode_pcm(clip, cfg.dub.sample_rate, begin, finish)))
        if finish - begin > room + 0.05:
            log.warning(
                "S6[%s]: cue %d đọc %.1fs nhưng chỉ có %.1fs — sẽ lấn sang cue sau",
                lang, seg.id, finish - begin, room,
            )

    dst = Path(out_dir) / f"{work_dir.name}.{lang}.mp3"
    assemble(clips, cfg.dub.sample_rate, dst, total_sec, cfg.dub.min_gap_sec)
    log.info("S6[%s] xong: %s (%d/%d cue phải tăng tốc)", lang, dst.name, sped_up, len(segments))
    return dst
