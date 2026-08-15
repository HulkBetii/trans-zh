"""One browser per Chrome profile, shared by every caller in the process.

Two constraints force this shape:

* Chromium refuses to open the same user-data directory twice, so the browser has
  to be a process-wide singleton even when ``zhsub batch -j 4`` runs several jobs
  in parallel threads.
* Playwright's async API must stay on one event loop, and the pipeline is
  synchronous throughout. Hence a dedicated loop thread and a blocking bridge.

Calls are serialised behind one lock and one tab. Parallel tabs would interleave
into a single garbled composer, and even done correctly they only make one
account hit its message quota faster — so ``zhsub batch -j 4`` still runs its ASR
and render work in parallel while the ChatGPT calls queue up.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from contextlib import contextmanager
from pathlib import Path

from ...config import ChatGPTWebConfig
from .chat import NEW_CHAT_URL
from .login import NAVIGATION_TIMEOUT_MS, ChatGPTLoginError, ensure_logged_in, verify_logged_in

log = logging.getLogger(__name__)

_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]
_VIEWPORT = {"width": 1280, "height": 800}
# Chromium leaves these behind when killed and then refuses to start on the profile.
_STALE_LOCKS = ("SingletonLock", "SingletonCookie", "SingletonSocket")
_BROWSER_CLOSED_FRAGMENTS = (
    "browser has been closed",
    "browser closed",
    "browser has disconnected",
    "connection closed",
    "target page, context or browser has been closed",
)

_sessions: dict[Path, BrowserSession] = {}
_sessions_lock = threading.Lock()


class BrowserSessionUnavailable(RuntimeError):
    """The browser stopped and the current LLM call should be retried."""


def _browser_stopped_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return any(fragment in message for fragment in _BROWSER_CLOSED_FRAGMENTS)


def _load_account(path: Path) -> dict:
    """Credentials are optional — without them login falls back to a human doing it
    once in the persistent profile, which is the recommended setup."""
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise ChatGPTLoginError(f"Không đọc được file tài khoản {path}: {exc}") from exc


async def _close_quietly(page) -> None:
    try:
        await page.close()
    except Exception:  # noqa: BLE001 - the tab is already gone, which is the goal
        pass


class BrowserSession:
    def __init__(self, profile_dir: Path, account_file: Path, manual_login_timeout_sec: float) -> None:
        self.profile_dir = profile_dir
        self.account_file = account_file
        self.manual_login_timeout_sec = manual_login_timeout_sec
        self._loop = asyncio.new_event_loop()
        self._lock = threading.Lock()
        self._page = None
        self._ctx = None
        self._pw = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        ready = threading.Event()
        self._thread = threading.Thread(
            target=self._serve_loop, args=(ready,), daemon=True, name="zhsub-chatgpt"
        )
        self._thread.start()
        ready.wait()
        try:
            self.run(self._launch())
        except Exception:
            self.stop()
            raise

    def _serve_loop(self, ready: threading.Event) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.call_soon(ready.set)
        try:
            self._loop.run_forever()
        finally:
            self._loop.close()

    def run(self, coro):
        """Block the calling thread until ``coro`` finishes on the browser loop.

        No timeout here on purpose: every Playwright call already carries one, and
        a timeout at this level would abandon a coroutine still driving the browser.
        """
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    async def _launch(self) -> None:
        from playwright.async_api import async_playwright

        self.profile_dir.mkdir(parents=True, exist_ok=True)
        for name in _STALE_LOCKS:
            (self.profile_dir / name).unlink(missing_ok=True)

        log.info("Mở Chrome với profile %s", self.profile_dir)
        self._pw = await async_playwright().start()
        # headless=False is not a preference: the Cloudflare check in front of
        # chatgpt.com never clears in headless mode, so a hidden browser cannot log in.
        self._ctx = await self._pw.chromium.launch_persistent_context(
            str(self.profile_dir), headless=False, args=_LAUNCH_ARGS, viewport=_VIEWPORT
        )
        page = self._ctx.pages[0] if self._ctx.pages else await self._ctx.new_page()
        await ensure_logged_in(page, _load_account(self.account_file), self.manual_login_timeout_sec)
        self._page = page

    async def _is_alive(self) -> bool:
        browser = self._ctx.browser if self._ctx is not None else None
        return bool(browser and browser.is_connected())

    def is_alive(self) -> bool:
        if self._loop.is_closed() or not self._loop.is_running() or self._ctx is None:
            return False
        try:
            return bool(self.run(self._is_alive()))
        except Exception as exc:  # noqa: BLE001 - a dead Playwright transport is expected here
            log.info("Phiên ChatGPT Playwright không còn hoạt động: %s", exc)
            return False

    async def _shutdown(self) -> None:
        if self._ctx is not None:
            try:
                await self._ctx.close()
            except Exception as exc:  # noqa: BLE001 - the browser may already be gone
                log.info("Không cần đóng context ChatGPT đã dừng: %s", exc)
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception as exc:  # noqa: BLE001 - transport shutdown is best effort
                log.info("Không cần dừng Playwright đã đóng: %s", exc)
        self._page = None
        self._ctx = None
        self._pw = None

    def stop(self) -> None:
        if self._loop.is_running():
            try:
                self.run(self._shutdown())
            except Exception as exc:  # noqa: BLE001 - shutdown follows a crashed browser
                log.warning("Không thể dọn hoàn toàn phiên ChatGPT Playwright: %s", exc)
            finally:
                self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout=5)

    async def _new_page(self):
        """Reopen the tab after it died. Cookies live in the context, so it only
        needs a sanity check rather than another login."""
        page = await self._ctx.new_page()
        await page.goto(NEW_CHAT_URL, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        if not (await verify_logged_in(page))["logged_in"]:
            await _close_quietly(page)
            raise ChatGPTLoginError("Phiên ChatGPT đã hết hạn — chạy `zhsub chatgpt-login` rồi thử lại.")
        return page

    @contextmanager
    def page(self):
        """Hold the one tab for the whole exchange.

        The lock spans the entire conversation turn, not just handing the tab over:
        that is what serialises concurrent callers instead of letting them type over
        each other.
        """
        with self._lock:
            try:
                if self._page is None or self._page.is_closed():
                    self._page = self.run(self._new_page())
            except Exception as exc:
                if not self.is_alive():
                    self._page = None
                    raise BrowserSessionUnavailable(
                        "Phiên ChatGPT Playwright đã tắt trong lúc mở tab."
                    ) from exc
                raise
            try:
                yield self._page
            except Exception as exc:
                # Only a genuinely closed tab is discarded. Closing it on *any* failure
                # was catastrophic: launch_persistent_context shuts Chromium down when
                # its last page closes, so one slow answer killed the browser and every
                # later call in every parallel job died with "browser has been closed".
                # A slow or malformed reply leaves a perfectly usable tab behind.
                if self._page.is_closed() or _browser_stopped_error(exc):
                    self._page = None
                    if not self.is_alive():
                        raise BrowserSessionUnavailable(
                            "Phiên ChatGPT Playwright đã tắt khi đang xử lý yêu cầu."
                        ) from exc
                raise


def get_session(cfg: ChatGPTWebConfig) -> BrowserSession:
    """Return the process-wide session for ``cfg.profile_dir``, starting it if needed.

    The lock is held across the launch — including a wait for manual login — so
    that a second thread cannot race into launching a second browser on the same
    profile directory, which Chromium would reject outright.
    """
    profile_dir = Path(cfg.profile_dir).resolve()
    with _sessions_lock:
        session = _sessions.get(profile_dir)
        if session is not None and not session.is_alive():
            log.warning("Phiên ChatGPT Playwright đã tắt — tự khởi động lại")
            session.stop()
            _sessions.pop(profile_dir, None)
            session = None
        if session is None:
            log.info("Tự khởi động ChatGPT Playwright cho profile %s", profile_dir)
            session = BrowserSession(
                profile_dir, Path(cfg.account_file), cfg.manual_login_timeout_sec
            )
            try:
                session.start()
            except ChatGPTLoginError:
                raise
            except Exception as exc:
                raise BrowserSessionUnavailable(
                    f"Không thể tự khởi động ChatGPT Playwright: {exc}"
                ) from exc
            _sessions[profile_dir] = session
        return session
