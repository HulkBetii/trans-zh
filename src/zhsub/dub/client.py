"""Client cho API text-to-speech của OpenSpeaker (api.ai33.pro).

Hạn mức tính theo **người dùng** chứ không theo credential, nên script này và app
web dùng chung một túi token. Vì thế 429 và 503 `server_busy` là chuyện thường chứ
không phải ngoại lệ — bỏ qua chúng thì response trả về là JSON lỗi, và code đọc
thẳng ``["status"]`` sẽ nổ KeyError giữa chừng một job 184 lần gọi.
"""

from __future__ import annotations

import logging
import os
import random
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

# Bậc thang thay vì một khoảng cố định. Phần lớn câu tổng hợp xong trong 2-5 giây,
# nên chờ cứng 8 giây là mỗi cue mất vài giây vô ích — trên 342 cue thành cả chục
# phút. Nhưng cũng không quay về 4 giây cố định: câu dài mà hỏi dồn thì lại dội vào
# đúng endpoint hay quá tải nhất. Hỏi sớm rồi giãn dần lấy được cả hai.
POLL_SCHEDULE_SEC = (2.0, 3.0, 4.0, 6.0, 8.0)
POLL_MAX_ATTEMPTS = 60
POLL_TIMEOUT_SEC = sum(
    POLL_SCHEDULE_SEC[min(attempt, len(POLL_SCHEDULE_SEC) - 1)]
    for attempt in range(POLL_MAX_ATTEMPTS)
)
# Endpoint /v1/task/{id} của nhà cung cấp có lúc trả 503 kéo dài hàng chục phút —
# kiểm bằng một request thủ công đơn lẻ cũng 503, tức là hỏng thật chứ không phải
# do gọi quá tay. Một job lồng tiếng chạy cả tiếng, nên chờ lâu vẫn rẻ hơn chết.
RETRY_MAX_ATTEMPTS = 20
RETRY_BASE_SEC = 3.0
RETRY_CAP_SEC = 120.0
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
        self._api_key = api_key
        self._client = httpx.Client(timeout=timeout_sec)

    def _request(self, method: str, path: str, **kwargs) -> Any:
        delay = RETRY_BASE_SEC
        request_headers = dict(kwargs.pop("headers", {}) or {})
        request_headers["xi-api-key"] = self._api_key
        for attempt in range(RETRY_MAX_ATTEMPTS):
            try:
                resp = self._client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=request_headers,
                    **kwargs,
                )
            except httpx.HTTPError as exc:
                raise DubError(f"lỗi mạng khi gọi {path}: {exc}") from exc

            if resp.status_code == 429:
                # Retry-After là con số nhà cung cấp đưa ra; jitter để nhiều luồng
                # không cùng thức dậy một lúc rồi lại đâm vào nhau.
                try:
                    retry_after = float(resp.headers.get("Retry-After", delay))
                except (TypeError, ValueError):
                    retry_after = delay
                wait = max(0.0, retry_after) + random.random()
                _log_retry("chạm hạn mức", wait, attempt)
                time.sleep(wait)
                delay = min(delay * 2, RETRY_CAP_SEC)
                continue

            # Gateways sometimes return an HTML 503 page. Retry by status before
            # attempting JSON parsing so transient provider outages stay retryable.
            if resp.status_code == 503:
                wait = delay + random.random()
                _log_retry("server bận", wait, attempt)
                time.sleep(wait)
                delay = min(delay * 2, RETRY_CAP_SEC)
                continue

            try:
                body = resp.json()
            except ValueError as exc:
                raise DubError(f"{path} trả về không phải JSON: {resp.text[:200]}") from exc

            # server_busy là áp lực dung lượng tạm thời, không phải lỗi của người gọi.
            if isinstance(body, dict) and body.get("code") == "server_busy":
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
        for attempt in range(POLL_MAX_ATTEMPTS):
            time.sleep(POLL_SCHEDULE_SEC[min(attempt, len(POLL_SCHEDULE_SEC) - 1)])
            task = self._request("GET", f"/v1/task/{task_id}")
            status = task.get("status")
            if status == "done":
                return task.get("metadata") or {}
            if status == "error":
                raise DubError(f"task {task_id} hỏng: {task.get('error_message')}")
        raise DubError(f"task {task_id} chưa xong sau {POLL_TIMEOUT_SEC:.0f}s")

    def download(self, url: str, dst: Path) -> Path:
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(f"{dst.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
        try:
            try:
                with self._client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    with open(tmp, "wb") as f:
                        for chunk in resp.iter_bytes():
                            f.write(chunk)
                        f.flush()
                        os.fsync(f.fileno())
            except httpx.HTTPError as exc:
                raise DubError(f"không tải được audio từ {url}: {exc}") from exc
            if not tmp.is_file() or tmp.stat().st_size == 0:
                raise DubError(f"audio tải về từ {url} bị rỗng")
            os.replace(tmp, dst)
            return dst
        finally:
            tmp.unlink(missing_ok=True)

    def voices(
        self,
        provider: str = "vbee",
        *,
        language: str = "Vietnamese",
        search: str = "",
        page: int = 1,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        """Return the provider voice library without exposing credentials."""
        if page < 1 or not 1 <= page_size <= 100:
            raise ValueError("invalid voice page")
        params: dict[str, Any] = {
            "provider": provider,
            "language": language,
            "page": page,
            "page_size": page_size,
        }
        if search.strip():
            params["search"] = search.strip()
        body = self._request("GET", "/v3/voices", params=params)
        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]
        if isinstance(body, dict):
            for key in ("voices", "data", "items"):
                items = body.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
        raise DubError(f"/v3/voices trả về dữ liệu không hợp lệ: {body}")

    def credits(self) -> int:
        return int(self._request("GET", "/v1/credits").get("credits", 0))

    def close(self) -> None:
        self._client.close()
