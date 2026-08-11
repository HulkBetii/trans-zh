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
RETRY_MAX_ATTEMPTS = 8
RETRY_BASE_SEC = 3.0
RETRY_CAP_SEC = 60.0


class DubError(RuntimeError):
    pass


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
                log.info("S6: chạm hạn mức, chờ %.1fs (lần %d)", wait, attempt + 1)
                time.sleep(wait)
                delay = min(delay * 2, RETRY_CAP_SEC)
                continue

            try:
                body = resp.json()
            except ValueError as exc:
                raise DubError(f"{path} trả về không phải JSON: {resp.text[:200]}") from exc

            # server_busy là áp lực dung lượng tạm thời, không phải lỗi của người gọi.
            if resp.status_code == 503 or body.get("code") == "server_busy":
                time.sleep(delay + random.random())
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
