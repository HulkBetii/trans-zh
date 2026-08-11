"""S6 lồng tiếng — phần tính toán, không cần API lẫn ffmpeg."""

from __future__ import annotations

from pathlib import Path

from zhsub.config import Config
from zhsub.dub.audio import assemble
from zhsub.models import Segment
from zhsub.stages.s6_dub import _rooms, plan_speed


def _cfg() -> Config:
    cfg = Config()
    cfg.dub.overhead_sec = 0.15
    cfg.dub.sec_per_syllable = 0.217
    cfg.dub.max_speed = 1.15
    return cfg


def test_a_cue_with_room_to_spare_is_read_at_normal_speed():
    # 10 âm tiết -> 0.15 + 2.17 = 2.32s, thừa chỗ trong 5s
    assert plan_speed(10, 5.0, _cfg()) == 1.0


def test_a_tight_cue_is_sped_up_just_enough():
    # 20 âm tiết -> 4.49s, chỉ có 4.0s
    speed = plan_speed(20, 4.0, _cfg())

    assert 1.0 < speed < 1.15
    assert round(speed, 2) == round(4.49 / 4.0, 2)


def test_speed_is_capped_so_the_voice_never_sounds_rushed():
    """Không nới trần: đo trên video thật, sau khi cắt lặng cue chật nhất chỉ cần
    x1.11, nên chạm trần nghĩa là có gì đó khác thường chứ không phải cần nhanh hơn."""
    assert plan_speed(200, 1.0, _cfg()) == 1.15


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
