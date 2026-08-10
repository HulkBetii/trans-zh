"""Get a Playwright page to a logged-in chatgpt.com.

Ported from the auto_YT project (``src/auto_yt/services/chatgpt_login.py``). Three
ways in, tried in order, because they cost wildly different amounts of trouble:

1. The persistent Chrome profile already holds a live session — the normal case
   after the first run, and the only one that needs no secrets on disk.
2. Cookies saved in the account file.
3. Full email/password/TOTP form filling.

If none apply the browser is left open on the login page and we simply wait for a
human. That is the recommended setup: it keeps the ChatGPT password out of the
repository entirely, and device verification or a captcha — both of which the
automated path cannot clear — only ever needs solving once per profile.
"""

from __future__ import annotations

import asyncio
import json
import logging

log = logging.getLogger(__name__)

CHATGPT_URL = "https://chatgpt.com"
AUTH_DOMAIN = "auth.openai.com"

NAVIGATION_TIMEOUT_MS = 60_000
CLOUDFLARE_WAIT_MS = 15_000
LOGIN_BUTTON_TIMEOUT_MS = 10_000
INPUT_TIMEOUT_MS = 10_000
POST_LOGIN_TIMEOUT_MS = 45_000
MANUAL_LOGIN_POLL_SEC = 3.0

# Stable attributes only — react-aria-* ids change on every ChatGPT deploy.
SEL_EMAIL_INPUT = 'input[name="email"], input[autocomplete="email"]'
SEL_PASSWORD_INPUT = 'input[name="current-password"], input[name="password"], input[type="password"]'
SEL_SUBMIT_BUTTON = 'button[type="submit"]'
SEL_MFA_INPUT = 'input[inputmode="numeric"], input[name="code"], input[autocomplete="one-time-code"]'
SEL_LOGIN_BUTTON = '[data-testid="login-button"], button:has-text("Log in")'
SEL_PROFILE_BUTTON = '[data-testid="accounts-profile-button"]'
SEL_BOOTSTRAP = "script#client-bootstrap"
SEL_CLOSE_SIDEBAR = '[data-testid="close-sidebar-button"]'
SEL_COOKIE_BANNER_ACCEPT = 'button:has-text("Reject non-essential"), button:has-text("Accept all")'

# A wrong password renders as plain text inside li._error_* — no role=alert, no
# data-testid — so the error text itself has to be matched.
SEL_ERROR_TEXT = (
    "text=/Incorrect email address or password|"
    "Invalid code|Incorrect code|Code expired|Try again|"
    "Verify you are human|Checking if the site connection is secure|"
    "verify your identity|verify your email|device verification/i"
)
SEL_ERROR_CONTAINERS = (
    '[role="alert"]',
    '[data-testid*="error"]',
    '[aria-live="assertive"]',
    '[aria-live="polite"]',
    'li[class*="error"]',
    'div[class*="error"]',
    SEL_ERROR_TEXT,
)
SEL_CLOUDFLARE_OR_CAPTCHA = (
    'iframe[src*="turnstile"], '
    'iframe[src*="challenges"], '
    'iframe[src*="captcha"], '
    'iframe[title*="Cloudflare"], '
    'iframe[title*="Widget"], '
    "#cf-turnstile, "
    ".cf-turnstile, "
    '[data-testid*="captcha"]'
)

_CLOUDFLARE_TITLES = {"just a moment...", "checking your browser"}
_ERROR_FRAGMENTS = (
    "incorrect email address or password",
    "incorrect password",
    "invalid code",
    "incorrect code",
    "code expired",
    "try again",
    "verify your email",
    "verify your identity",
    "device verification",
    "new device",
    "verify you are human",
    "checking if the site connection is secure",
    "cloudflare",
    "captcha",
)


class ChatGPTLoginError(RuntimeError):
    """Login failed for a known reason. Never retryable — a retry loop would just
    re-enter the same wrong password."""


class ChatGPTLoginCredentialError(ChatGPTLoginError):
    """Wrong email or password."""


class ChatGPTLoginMFAError(ChatGPTLoginError):
    """MFA prompted but no ``totp_secret`` configured, or the code was rejected."""


class ChatGPTLoginVerifyError(ChatGPTLoginError):
    """Cloudflare / captcha / anti-bot wall."""


class ChatGPTLoginDeviceVerificationError(ChatGPTLoginError):
    """New-device or email confirmation required — only a human can clear it."""


async def _dismiss_cookie_banner(page) -> None:
    """The consent banner overlays the login button, so clicks land on it instead."""
    try:
        btn = page.locator(SEL_COOKIE_BANNER_ACCEPT).first
        if await btn.is_visible(timeout=2_000):
            await btn.click()
            log.info("Đã đóng banner cookie")
            await asyncio.sleep(0.5)
    except Exception:  # noqa: BLE001 - absence of the banner is the common case
        pass


async def _is_cloudflare_or_captcha(page) -> bool:
    try:
        if (await page.title()).strip().lower() in _CLOUDFLARE_TITLES:
            return True
    except Exception:  # noqa: BLE001 - navigation in flight
        pass

    try:
        if await page.locator(SEL_CLOUDFLARE_OR_CAPTCHA).count() > 0:
            return True
    except Exception:  # noqa: BLE001
        pass

    try:
        body = (await page.locator("body").first.inner_text(timeout=1_000)).lower()
        return any(
            text in body
            for text in (
                "verify you are human",
                "checking if the site connection is secure",
                "cloudflare",
                "captcha",
            )
        )
    except Exception:  # noqa: BLE001
        return False


async def _wait_past_cloudflare(page, timeout_ms: int = CLOUDFLARE_WAIT_MS) -> None:
    """The JS challenge self-resolves in a headed browser; this only bounds the wait."""
    deadline = asyncio.get_event_loop().time() + timeout_ms / 1000
    while asyncio.get_event_loop().time() < deadline:
        if not await _is_cloudflare_or_captcha(page):
            return
        await asyncio.sleep(1)
    raise ChatGPTLoginVerifyError(
        "Cloudflare không tự giải sau "
        f"{timeout_ms // 1000}s. Trình duyệt phải chạy headed và không bị nhận diện là bot."
    )


async def _visible_error_text(page) -> str | None:
    for selector in SEL_ERROR_CONTAINERS:
        try:
            locator = page.locator(selector).first
            if await locator.is_visible(timeout=800):
                text = (await locator.inner_text(timeout=1_000)).strip()
                if text and any(fragment in text.lower() for fragment in _ERROR_FRAGMENTS):
                    return text
        except Exception:  # noqa: BLE001 - selector absent on this page
            continue
    return None


async def _raise_known_auth_error(page, stage: str) -> None:
    """Turn whatever the auth page is showing into a precise exception.

    Precision matters because the caller's response differs: a captcha or device
    check needs a human at the keyboard, a wrong password needs the config fixed.
    """
    if await _is_cloudflare_or_captcha(page):
        raise ChatGPTLoginVerifyError(
            f"Gặp Cloudflare/captcha ({stage}). URL: {page.url}; title: {await page.title()}"
        )

    text = await _visible_error_text(page)
    if not text:
        return

    lower = text.lower()
    if "incorrect email address or password" in lower or "incorrect password" in lower:
        raise ChatGPTLoginCredentialError(f"Sai email hoặc mật khẩu ({stage}): {text}")
    if any(w in lower for w in ("invalid code", "incorrect code", "code expired", "try again")):
        raise ChatGPTLoginMFAError(f"Mã TOTP bị từ chối hoặc hết hạn ({stage}): {text}")
    if any(w in lower for w in ("verify your email", "verify your identity", "device verification", "new device")):
        raise ChatGPTLoginDeviceVerificationError(
            f"Cần xác minh thiết bị/email ({stage}): {text}"
        )
    if any(w in lower for w in ("verify you are human", "cloudflare", "captcha")):
        raise ChatGPTLoginVerifyError(f"Cần xác minh con người ({stage}): {text}")

    raise ChatGPTLoginError(f"Lỗi đăng nhập ({stage}): {text}")


async def _fill_and_submit(page, selector: str, value: str) -> None:
    field = page.locator(selector).first
    await field.wait_for(state="visible", timeout=INPUT_TIMEOUT_MS)
    await field.fill(value)
    await asyncio.sleep(0.3)
    try:
        await field.press("Enter")
    except Exception:  # noqa: BLE001 - some steps only accept the button
        await page.locator(SEL_SUBMIT_BUTTON).first.click(force=True)


async def _wait_for_auth_redirect_or_email_form(page, timeout_ms: int = 8_000) -> None:
    """ChatGPT either redirects to auth.openai.com or opens an inline email form.

    Waiting for both at once keeps the inline flow from paying the full redirect
    timeout on every login.
    """
    auth_task = asyncio.create_task(page.wait_for_url(f"**/{AUTH_DOMAIN}/**", timeout=timeout_ms))
    email_task = asyncio.create_task(
        page.locator(SEL_EMAIL_INPUT).first.wait_for(state="visible", timeout=timeout_ms)
    )
    done, pending = await asyncio.wait(
        {auth_task, email_task}, timeout=timeout_ms / 1000, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending:
        task.cancel()
    for task in done:
        try:
            await task
            return
        except Exception:  # noqa: BLE001 - the other branch may still win
            continue
    raise ChatGPTLoginVerifyError(
        "Bấm Log in nhưng không thấy form email lẫn redirect sang trang auth. "
        f"URL: {page.url}; title: {await page.title()}"
    )


async def _bootstrap_data(page) -> dict | None:
    try:
        return json.loads(await page.locator(SEL_BOOTSTRAP).first.inner_text(timeout=3_000))
    except Exception:  # noqa: BLE001 - script absent while the SPA boots
        return None


async def verify_logged_in(page) -> dict:
    """Return ``{"logged_in": bool, "user": dict | None}``.

    Reads ``client-bootstrap`` first because it is authoritative and available
    before the SPA finishes rendering; the profile button is a slower fallback for
    the deploys where that script is missing.
    """
    bootstrap = await _bootstrap_data(page)
    if bootstrap:
        session = bootstrap.get("session")
        if bootstrap.get("authStatus") == "logged_in" and isinstance(session, dict):
            user = session.get("user") or {}
            return {
                "logged_in": True,
                "user": {
                    "email": user.get("email", ""),
                    "name": user.get("name", ""),
                    "plan": user.get("plan", ""),
                },
            }
        if bootstrap.get("authStatus") == "logged_out":
            return {"logged_in": False, "user": None}

    try:
        await page.locator(SEL_PROFILE_BUTTON).first.wait_for(state="visible", timeout=5_000)
        if AUTH_DOMAIN not in page.url:
            return {"logged_in": True, "user": {"email": "", "name": "", "plan": ""}}
    except Exception:  # noqa: BLE001 - button absent means logged out
        pass

    return {"logged_in": False, "user": None}


async def login_gpt_auto(account: dict, page) -> dict:
    """Fill the login forms with ``email`` / ``password`` / optional ``totp_secret``.

    The browser must be headed: the Cloudflare check in front of chatgpt.com does
    not clear in headless mode.
    """
    email = str(account.get("email", "")).strip()
    password = str(account.get("password", ""))
    totp_secret = str(account.get("totp_secret") or "").strip() or None

    if not email or not password:
        raise ChatGPTLoginError("Thiếu 'email' hoặc 'password' trong file tài khoản.")

    log.info("Đăng nhập ChatGPT bằng %s", email)
    await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
    await _wait_past_cloudflare(page)
    await asyncio.sleep(1)

    verify = await verify_logged_in(page)
    if verify["logged_in"]:
        return {"success": True, "cookies": await page.context.cookies(), "user": verify["user"]}

    await _dismiss_cookie_banner(page)
    try:
        close_sidebar = page.locator(SEL_CLOSE_SIDEBAR).first
        if await close_sidebar.is_visible(timeout=1_000):
            await close_sidebar.click()
            await asyncio.sleep(0.5)
    except Exception:  # noqa: BLE001 - sidebar already closed
        pass

    login_btn = page.locator(SEL_LOGIN_BUTTON).first
    try:
        await login_btn.wait_for(state="visible", timeout=LOGIN_BUTTON_TIMEOUT_MS)
        await login_btn.click()
    except Exception as exc:  # noqa: BLE001
        # Overlays swallow the pointer event; a dispatched click still reaches the
        # handler. Give up only if the email form never shows either way.
        try:
            await page.locator(SEL_EMAIL_INPUT).first.wait_for(state="visible", timeout=3_000)
        except Exception:  # noqa: BLE001
            try:
                await login_btn.dispatch_event("click")
                await asyncio.sleep(1)
                await page.locator(SEL_EMAIL_INPUT).first.wait_for(state="visible", timeout=5_000)
            except Exception:  # noqa: BLE001
                raise ChatGPTLoginVerifyError(
                    "Không bấm được nút Log in và form email cũng không hiện. "
                    f"URL: {page.url}; title: {await page.title()}"
                ) from exc

    await _wait_for_auth_redirect_or_email_form(page)
    await asyncio.sleep(0.2)

    await _fill_and_submit(page, SEL_EMAIL_INPUT, email)
    await asyncio.sleep(1.5)
    await _raise_known_auth_error(page, "email")

    try:
        await _fill_and_submit(page, SEL_PASSWORD_INPUT, password)
    except Exception as exc:  # noqa: BLE001
        await _raise_known_auth_error(page, "password-form")
        raise ChatGPTLoginError(f"Không thấy ô mật khẩu: {exc}") from exc

    await asyncio.sleep(2)
    await _raise_known_auth_error(page, "password")

    try:
        mfa_input = page.locator(SEL_MFA_INPUT).first
        await mfa_input.wait_for(state="visible", timeout=5_000)
        if not totp_secret:
            raise ChatGPTLoginMFAError("ChatGPT hỏi mã MFA nhưng file tài khoản không có totp_secret.")
        import pyotp  # lazy: only accounts with MFA need the dependency

        await mfa_input.fill(pyotp.TOTP(totp_secret).now())
        await asyncio.sleep(0.3)
        await page.locator(SEL_SUBMIT_BUTTON).first.click()
        await asyncio.sleep(2)
        await _raise_known_auth_error(page, "mfa")
    except ChatGPTLoginError:
        raise
    except Exception:  # noqa: BLE001 - no MFA form is the normal path
        pass

    try:
        await page.wait_for_url("**/chatgpt.com/**", timeout=POST_LOGIN_TIMEOUT_MS)
    except Exception:  # noqa: BLE001
        if AUTH_DOMAIN in page.url:
            await _raise_known_auth_error(page, "redirect")
            raise ChatGPTLoginVerifyError(f"Vẫn kẹt ở {AUTH_DOMAIN} sau khi hết giờ. URL: {page.url}")

    await asyncio.sleep(2)
    verify = await verify_logged_in(page)
    if not verify["logged_in"]:
        # NextAuth hydration lags behind the redirect often enough to be worth one reload.
        await page.reload(wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
        await asyncio.sleep(3)
        verify = await verify_logged_in(page)

    if not verify["logged_in"]:
        raise ChatGPTLoginError(
            "Đi hết luồng đăng nhập nhưng authStatus vẫn không phải 'logged_in' — "
            "nhiều khả năng bị chặn anti-bot."
        )

    cookies = await page.context.cookies()
    log.info("Đăng nhập xong, lấy được %d cookie cho %s", len(cookies), email)
    return {"success": True, "cookies": cookies, "user": verify["user"]}


async def restore_session(cookies: list[dict], page) -> dict:
    """Inject previously captured cookies. Same return shape as :func:`login_gpt_auto`."""
    await page.context.add_cookies(cookies)
    await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
    await _wait_past_cloudflare(page)
    await asyncio.sleep(2)

    verify = await verify_logged_in(page)
    if not verify["logged_in"]:
        raise ChatGPTLoginError("Cookie đã lưu hết hạn hoặc không hợp lệ.")
    return {"success": True, "cookies": await page.context.cookies(), "user": verify["user"]}


async def _wait_for_manual_login(page, timeout_sec: float) -> dict:
    log.warning(
        "Chưa đăng nhập ChatGPT. Đăng nhập tay trong cửa sổ trình duyệt vừa mở "
        "(chờ tối đa %.0f giây). Làm một lần, profile sẽ nhớ phiên.",
        timeout_sec,
    )
    deadline = asyncio.get_event_loop().time() + timeout_sec
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(MANUAL_LOGIN_POLL_SEC)
        verify = await verify_logged_in(page)
        if verify["logged_in"]:
            return verify
    raise ChatGPTLoginError(
        f"Hết {timeout_sec:.0f} giây chờ đăng nhập tay. Chạy `zhsub chatgpt-login` để đăng nhập trước."
    )


async def ensure_logged_in(page, account: dict, manual_timeout_sec: float) -> dict:
    """Bring ``page`` to a logged-in chatgpt.com, cheapest path first."""
    await page.goto(CHATGPT_URL, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
    await _wait_past_cloudflare(page)
    await asyncio.sleep(1)

    verify = await verify_logged_in(page)
    if verify["logged_in"]:
        log.info("Dùng lại phiên ChatGPT sẵn có trong profile (%s)", verify["user"].get("email") or "?")
        return verify

    cookies = account.get("session_cookie") or []
    if cookies:
        try:
            result = await restore_session(cookies, page)
            log.info("Khôi phục phiên từ %d cookie đã lưu", len(cookies))
            return result
        except ChatGPTLoginError as exc:
            log.warning("Khôi phục cookie thất bại: %s", exc)

    if account.get("email") and account.get("password"):
        return await login_gpt_auto(account, page)

    return await _wait_for_manual_login(page, manual_timeout_sec)
