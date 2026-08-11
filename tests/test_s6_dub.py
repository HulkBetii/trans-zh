"""S6 lồng tiếng — phần tính toán, không cần API lẫn ffmpeg."""

from __future__ import annotations

from pathlib import Path

from zhsub.config import Config
from zhsub.dub.audio import assemble
from zhsub.models import Segment
from zhsub.stages.s6_dub import _rooms, plan_speed


def _cfg(max_speed: float = 1.0) -> Config:
    cfg = Config()
    cfg.dub.overhead_sec = 0.15
    cfg.dub.sec_per_syllable = 0.217
    cfg.dub.base_speed = 1.0
    cfg.dub.max_speed = max_speed
    return cfg


def test_every_cue_is_read_at_the_base_speed():
    assert plan_speed(10, 5.0, _cfg()) == 1.0


def test_a_long_cue_is_never_sped_up_when_the_ceiling_equals_the_base():
    """Nghe thử bản tăng tốc tới 1.15x thì rõ là gấp gáp. Câu dài giờ lấn sang
    khoảng lặng phía sau chứ không bị nén."""
    assert plan_speed(200, 1.0, _cfg()) == 1.0


def test_raising_the_ceiling_lets_a_tight_cue_speed_up_again():
    """Trần vẫn dùng được nếu chấp nhận đánh đổi ngược lại."""
    speed = plan_speed(20, 4.0, _cfg(max_speed=1.15))

    assert round(speed, 2) == round(4.49 / 4.0, 2)


def test_the_ceiling_never_makes_a_cue_slower_than_the_base():
    assert plan_speed(1, 60.0, _cfg(max_speed=1.15)) == 1.0


def test_room_is_measured_to_the_next_cue_not_to_the_end_of_this_one():
    """Cue tràn 0.4s mà sau nó có khoảng lặng thì không lấn vào đâu cả. Dùng độ dài
    cue thay vì khoảng tới cue sau đã thổi số cue chật từ 17% lên 26%."""
    segments = [
        Segment(id=0, start=0.0, end=1.0, text_zh="a", token_range=(0, 1)),
        Segment(id=1, start=3.0, end=4.0, text_zh="b", token_range=(1, 2)),
    ]

    rooms = _rooms(segments, total_sec=10.0)

    assert rooms[0] == 3.0  # tới lúc cue sau bắt đầu, không phải 1.0
    assert rooms[1] == 7.0


def test_clips_land_at_their_timestamps_with_silence_between(tmp_path, monkeypatch):
    """Ghép theo mốc, không nối đuôi: tổng thời lượng đọc ngắn hơn timeline ~25%."""
    written: dict = {}

    def fake_run(cmd, input=None, capture_output=False):
        written["pcm"] = input

        class R:
            returncode = 0
            stderr = b""

        # ffmpeg ghi file đích ở cuối lệnh; tạo file rỗng để .replace() chạy được
        Path(cmd[-1]).write_bytes(b"")
        return R()

    monkeypatch.setattr("zhsub.dub.audio.subprocess.run", fake_run)
    monkeypatch.setattr("zhsub.dub.audio._require", lambda tool: tool)

    sr = 1000
    tone = b"\x01\x02" * 500  # 0.5 giây
    assemble([(0.0, tone), (2.0, tone)], sr, tmp_path / "out.mp3", total_sec=3.0)

    pcm = written["pcm"]
    assert len(pcm) == 3 * sr * 2
    assert pcm[0:1000] == tone                      # cue 1 tại giây 0
    assert pcm[1000:4000] == b"\x00" * 3000         # im lặng ở giữa
    assert pcm[4000:5000] == tone                   # cue 2 tại giây 2


def test_a_pushed_back_clip_still_gets_its_breath(tmp_path, monkeypatch):
    """Câu lấn giờ bị đẩy lùi, nhưng không được dán khít vào đuôi câu trước.

    Nghe thử bản chưa có khoảng nghỉ: tốc độ đọc thì ổn mà chỗ giao giữa các câu
    gấp gáp hẳn, vì cắt lặng đã bỏ mất đuôi im lặng của nhà cung cấp.
    """
    written: dict = {}

    def fake_run(cmd, input=None, capture_output=False):
        written["pcm"] = input

        class R:
            returncode = 0
            stderr = b""

        Path(cmd[-1]).write_bytes(b"")
        return R()

    monkeypatch.setattr("zhsub.dub.audio.subprocess.run", fake_run)
    monkeypatch.setattr("zhsub.dub.audio._require", lambda tool: tool)

    sr = 1000
    tone = b"\x01\x02" * 1000  # 1.0 giây, dài hơn chỗ của nó
    # cue 2 lẽ ra bắt đầu ở giây 0.5, nhưng cue 1 chiếm tới giây 1.0
    assemble([(0.0, tone), (0.5, tone)], sr, tmp_path / "out.mp3", total_sec=4.0,
             min_gap_sec=0.25)

    pcm = written["pcm"]
    assert pcm[0:2000] == tone                  # cue 1 tại giây 0, dài 1.0s
    assert pcm[2000:2500] == b"\x00" * 500      # nghỉ 0.25s trước cue 2
    assert pcm[2500:4500] == tone               # cue 2 bắt đầu ở giây 1.25
