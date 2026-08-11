"""Client cho API text-to-speech của OpenSpeaker (api.ai33.pro).

Hạn mức tính theo **người dùng** chứ không theo credential, nên script này và app
web dùng chung một túi token. Vì thế 429 và 503 `server_busy` là chuyện thường chứ
không phải ngoại lệ — bỏ qua chúng thì response trả về là JSON lỗi, và code đọc
thẳng ``["status"]`` sẽ nổ KeyError giữa chừng một job 184 lần gọi.
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

POLL_INTERVAL_SEC = 4.0
POLL_MAX_ATTEMPTS = 90
# 12 chứ không phải 8: một job lồng tiếng là 184 lần gọi kéo dài nửa tiếng, và một
# đợt `server_busy` dài hơn 4 phút rưỡi từng giết cả job ở cue thứ 82. Kiên nhẫn
# thêm rẻ hơn nhiều so với dừng giữa chừng, kể cả khi cue đã xong được lưu lại.
RETRY_MAX_ATTEMPTS = 12
RETRY_BASE_SEC = 3.0
RETRY_CAP_SEC = 60.0
# Vài lần đầu là chuyện thường, im lặng. Quá ngưỡng này thì có gì đó không ổn và
# người chạy cần biết trước khi job đứng im hàng phút.
RETRY_NOISY_AFTER = 3


class DubError(RuntimeError):
    pass


def _log_retry(reason: str, wait_sec: float, attempt: int) -> None:
    """Im lặng vài lần đầu, kêu to khi bắt đầu bất thường.

    Không có dòng này thì một đợt bị chặn dài chỉ hiện ra dưới dạng job đứng im
    hàng phút không rõ lý do — đúng cảnh đã gặp khi chạy 8 luồng.
    """
    if attempt + 1 >= RETRY_NOISY_AFTER:
        log.warning("S6: %s, chờ %.0fs rồi thử lại (lần %d/%d)",
                    reason, wait_sec, attempt + 1, RETRY_MAX_ATTEMPTS)
    else:
        log.debug("S6: %s, chờ %.1fs (lần %d)", reason, wait_sec, attempt + 1)


class SpeechClient:
    def __init__(self, base_url: str, api_key: str, timeout_sec: float = 180.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(timeout=timeout_sec, headers={"xi-api-key": api_key})

    def _request(self, method: str, path: str, **kwargs) -> dict:
        delay = RETRY_BASE_SEC
        for attempt in range(RETRY_MAX_ATTEMPTS):
            try:
                resp = self._client.request(method, f"{self.base_url}{path}", **kwargs)
            except httpx.HTTPError as exc:
                raise DubError(f"lỗi mạng khi gọi {path}: {exc}") from exc

            if resp.status_code == 429:
                # Retry-After là con số nhà cung cấp đưa ra; jitter để nhiều luồng
                # không cùng thức dậy một lúc rồi lại đâm vào nhau.
                wait = float(resp.headers.get("Retry-After", delay)) + random.random()
                _log_retry("chạm hạn mức", wait, attempt)
                time.sleep(wait)
                delay = min(delay * 2, RETRY_CAP_SEC)
                continue

            try:
                body = resp.json()
            except ValueError as exc:
                raise DubError(f"{path} trả về không phải JSON: {resp.text[:200]}") from exc

            # server_busy là áp lực dung lượng tạm thời, không phải lỗi của người gọi.
            if resp.status_code == 503 or body.get("code") == "server_busy":
                wait = delay + random.random()
                _log_retry("server bận", wait, attempt)
                time.sleep(wait)
                delay = min(delay * 2, RETRY_CAP_SEC)
                continue

            if resp.status_code >= 400:
                raise DubError(f"{path} HTTP {resp.status_code}: {resp.text[:300]}")
            return body

        raise DubError(f"{path}: hết {RETRY_MAX_ATTEMPTS} lần thử vì bị chặn liên tục")

    def synthesize(self, text: str, voice_id: str, speed: float) -> str:
        """Tạo task TTS, trả về task_id."""
        body = self._request(
            "POST", "/v3/text-to-speech",
            data={"text": text, "voice_id": voice_id, "speed": f"{speed:.2f}"},
        )
        task_id = body.get("task_id")
        if not task_id:
            raise DubError(f"không nhận được task_id: {body}")
        return task_id

    def wait(self, task_id: str) -> dict:
        """Chờ task xong, trả về metadata."""
        for _ in range(POLL_MAX_ATTEMPTS):
            time.sleep(POLL_INTERVAL_SEC)
            task = self._request("GET", f"/v1/task/{task_id}")
            status = task.get("status")
            if status == "done":
                return task.get("metadata") or {}
            if status == "error":
                raise DubError(f"task {task_id} hỏng: {task.get('error_message')}")
        raise DubError(f"task {task_id} chưa xong sau {POLL_MAX_ATTEMPTS * POLL_INTERVAL_SEC:.0f}s")

    def download(self, url: str, dst: Path) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        with self._client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dst, "wb") as f:
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        return dst

    def credits(self) -> int:
        return int(self._request("GET", "/v1/credits").get("credits", 0))

    def close(self) -> None:
        self._client.close()
