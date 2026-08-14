"""Nhịp hỏi trạng thái task."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from zhsub.dub.client import DubError, POLL_MAX_ATTEMPTS, POLL_SCHEDULE_SEC, SpeechClient


def _delay(attempt: int) -> float:
    return POLL_SCHEDULE_SEC[min(attempt, len(POLL_SCHEDULE_SEC) - 1)]


def test_the_first_question_comes_quickly():
    """Phần lớn câu tổng hợp xong trong 2-5 giây; chờ cứng 8 giây là mỗi cue mất
    vài giây vô ích, trên 342 cue thành cả chục phút."""
    assert _delay(0) <= 2.0


def test_the_gap_widens_and_then_holds():
    """Câu lâu mà hỏi dồn thì dội vào đúng endpoint hay quá tải nhất."""
    delays = [_delay(i) for i in range(len(POLL_SCHEDULE_SEC) + 3)]

    assert delays == sorted(delays)
    assert delays[-1] == POLL_SCHEDULE_SEC[-1]


def test_a_task_is_given_several_minutes_before_giving_up():
    total = sum(_delay(i) for i in range(POLL_MAX_ATTEMPTS))

    assert total > 300


def test_timeout_raises_a_dub_error_instead_of_name_error(monkeypatch):
    client = SpeechClient("https://example.test", "secret")
    monkeypatch.setattr("zhsub.dub.client.time.sleep", lambda _: None)
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: {"status": "running"})

    with pytest.raises(DubError, match="chưa xong sau"):
        client.wait("task-1")

    client.close()


class _StreamResponse:
    def __init__(self, chunks, error=None):
        self._chunks = chunks
        self._error = error

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        return None

    def iter_bytes(self):
        yield from self._chunks
        if self._error is not None:
            raise self._error


class _StreamClient:
    def __init__(self, response):
        self.response = response

    def stream(self, method, url):
        return self.response


def test_download_replaces_the_cache_only_after_a_complete_stream(tmp_path: Path):
    destination = tmp_path / "cue.mp3"
    destination.write_bytes(b"old")
    client = SpeechClient("https://example.test", "secret")
    client._client.close()
    client._client = _StreamClient(  # type: ignore[assignment]
        _StreamResponse([b"partial"], httpx.ReadError("broken stream"))
    )

    with pytest.raises(DubError, match="không tải được audio"):
        client.download("https://cdn.example/cue.mp3", destination)

    assert destination.read_bytes() == b"old"
    assert list(tmp_path.glob("*.tmp-*")) == []


def test_voice_library_accepts_wrapped_ai33_responses(monkeypatch):
    client = SpeechClient("https://example.test", "secret")
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: {"voices": [{"voice_id": "vbee-one"}]},
    )

    assert client.voices() == [{"voice_id": "vbee-one"}]

    client.close()
