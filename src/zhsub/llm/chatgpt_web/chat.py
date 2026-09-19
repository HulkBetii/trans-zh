"""Type a prompt into the ChatGPT composer and read the answer back.

Ported from the auto_YT project (``src/auto_yt/services/chat_gpt.py``), minus its
model-picker handling: this pipeline uses whatever mode the account was last left
in, so the flakiest selectors in the original are simply not needed here.

Two waits, not one, because the stop button disappears before the answer has
finished painting: a long JSON batch read at that moment comes back truncated,
and truncated JSON is indistinguishable from a model that ignored the schema.
"""

from __future__ import annotations

import asyncio
import logging
import re

from .login import NAVIGATION_TIMEOUT_MS

log = logging.getLogger(__name__)

NEW_CHAT_URL = "https://chatgpt.com/"
# A saved conversation gets this prefix once its first message lands. Temporary
# chats never do — which is why they are not used here: without a durable URL a
# thread cannot be resumed, and resuming is what keeps one stage's turns together
# when several jobs interleave on the single tab.
CONVERSATION_URL_PREFIX = "https://chatgpt.com/c/"

PROMPT_INPUT_SEL = "#prompt-textarea"
SEND_BUTTON_SELS = (
    'button[data-testid="send-button"]',
    'button[data-testid="composer-send-button"]',
    'button[data-testid*="send" i]',
    'button[aria-label*="send" i]',
    'button[aria-label*="gửi" i]',
    'button[aria-label="Send prompt"]',
    'button[aria-label="Send message"]',
    'button:has(svg path[d*="M15.192 8.906"])',
    'form button[type="submit"]',
)
ASSISTANT_MSG_SEL = '[data-message-author-role="assistant"]'
STOP_BUTTON_SEL = 'button[data-testid="stop-button"], button[aria-label="Stop generating"]'

# Model / thinking effort switcher selectors in composer
MODEL_SWITCHER_SELS = (
    'button:has-text("Thinking effort")',
    'button:has-text("Thinking")',
    'button:has-text("Instant")',
    'button[aria-label*="thinking" i]',
    'button[aria-label*="effort" i]',
    'button[data-testid*="model-switcher"]',
    'button[data-testid*="thinking"]',
)

INSTANT_MENU_ITEM_SELS = (
    '[role="menuitem"]:has-text("Instant")',
    '[role="menuitemradio"]:has-text("Instant")',
    '[role="option"]:has-text("Instant")',
    '[role="dialog"] button:has-text("Instant")',
    '[role="dialog"] div:has-text("Instant")',
    'div[data-radix-popper-content-wrapper] :text-is("Instant")',
)

POLL_INTERVAL_S = 2
TEXT_STABLE_SAMPLES = 3
TEXT_STABLE_INTERVAL_S = 2
TEXT_SETTLE_TIMEOUT_S = 45

# "Thought for 12s" ticks up on its own while the answer below it is already final.
_THINKING_TIMER_RE = re.compile(r"\bthought for \d+\s*(?:s|sec|seconds)\b", re.I)

# Full phrases only. A bare "upgrade to" would match the permanent "Upgrade plan"
# link in the sidebar and declare every single call rate-limited.
_LIMIT_FRAGMENTS = (
    "you've reached your limit",
    "you have reached your limit",
    "reached the current usage cap",
    "reached your daily limit",
    "you've hit your limit",
    "limit resets",
    "you're sending messages too quickly",
    "too many requests",
    "usage limit",
)


class ChatGPTResponseError(RuntimeError):
    """Sending the prompt or reading the answer failed. Retryable."""


class ChatGPTRateLimitError(RuntimeError):
    """The account is out of messages.

    Deliberately NOT a :class:`ChatGPTResponseError`: the provider only wraps that
    one into a retryable ``LLMError``, so this propagates untouched through every
    retry loop and every ``except LLMError`` in the stages, and stops the job on
    the spot. Retrying would only burn minutes against a wall that will not move
    until the quota resets.
    """


# Bộ lọc nội dung của ChatGPT. Câu trả lời dài đúng 168 ký tự và lặp lại y hệt suốt
# một video án mạng — đây là thông điệp cố định, không phải câu trả lời.
_REFUSAL_FRAGMENTS = (
    "can't be shown for safety reasons",
    "can’t be shown for safety reasons",
    "i can't help with that",
    "i can’t help with that",
    "i'm not able to help with that",
    "violates our usage policies",
    "against our content policy",
)


class ChatGPTContentRefusal(RuntimeError):
    """Bộ lọc nội dung chặn. Gửi lại y hệt cũng bị chặn y hệt.

    Không phải :class:`ChatGPTResponseError` để vòng thử lại không đụng vào, nhưng
    provider sẽ bọc thành ``NonRetryableLLMError`` — vẫn là ``LLMError`` nên stage
    gọi nó chạy được đường phục hồi riêng: S2 rơi xuống ngắt theo rule, S4 chia đôi
    batch. Chia đôi thực sự cứu được: đo trên một video án mạng, batch 60 câu bị
    chặn còn batch 30 câu lọt.
    """


def find_limit_fragment(text: str) -> str | None:
    lowered = text.lower()
    return next((f for f in _LIMIT_FRAGMENTS if f in lowered), None)


def find_refusal_fragment(text: str) -> str | None:
    lowered = text.lower()
    return next((f for f in _REFUSAL_FRAGMENTS if f in lowered), None)


async def _wait_streaming_done(page, timeout_s: int) -> None:
    """Wait for the stop button to disappear, i.e. generation finished."""
    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        try:
            if await page.locator(STOP_BUTTON_SEL).count() == 0:
                return
        except Exception:  # noqa: BLE001 - page navigated; the caller will notice
            return
        await asyncio.sleep(POLL_INTERVAL_S)
    raise ChatGPTResponseError(f"ChatGPT vẫn đang sinh chữ sau {timeout_s}s.")


async def _click_send(page) -> None:
    for _ in range(10):
        for sel in SEND_BUTTON_SELS:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=150) and await btn.is_enabled():
                    await btn.click()
                    return
            except Exception:
                continue
        await asyncio.sleep(0.15)
    log.warning("Không thấy nút gửi, dùng phím Enter thay thế")
    try:
        await page.locator(PROMPT_INPUT_SEL).first.focus()
        await page.keyboard.press("Enter")
    except Exception as exc:
        log.warning("Không thể bấm Enter: %s", exc)


async def _last_assistant_text(page) -> str:
    try:
        locator = page.locator(ASSISTANT_MSG_SEL)
        count = await locator.count()
        if count == 0:
            return ""
        text = await locator.nth(count - 1).evaluate(
            "(node) => node.innerText || node.textContent || ''", timeout=5_000
        )
        return str(text).strip()
    except Exception:  # noqa: BLE001 - mid-render DOM swap
        return ""


async def _raise_if_rate_limited(page) -> None:
    """Name the real cause when no answer arrives.

    A hit quota can appear as a banner or modal instead of an assistant message, in
    which case the message counter never moves and the only other explanation on
    offer is a timeout — which would send the caller looking for a slow network.
    """
    try:
        body = await page.locator("body").first.inner_text(timeout=2_000)
    except Exception:  # noqa: BLE001 - page busy; fall through to the timeout error
        return
    fragment = find_limit_fragment(body)
    if fragment:
        raise ChatGPTRateLimitError(f"ChatGPT hết hạn mức tin nhắn ({fragment!r}).")


async def _scroll_last_assistant_into_view(page) -> None:
    """Virtualised long answers only render the part near the viewport."""
    try:
        locator = page.locator(ASSISTANT_MSG_SEL)
        count = await locator.count()
        if count == 0:
            return
        await locator.nth(count - 1).scroll_into_view_if_needed(timeout=2_000)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    except Exception:  # noqa: BLE001 - best effort
        return


async def _wait_text_stable(page, timeout_s: int, require_stable: bool = False) -> str:
    """Poll the last answer until it stops changing across consecutive samples.

    ``require_stable`` refuses to hand back a still-moving answer. Normally the
    latest text is good enough, but when the caller already suspects the UI is
    stuck it must not be handed a half-written reply.
    """
    settle_timeout = min(TEXT_SETTLE_TIMEOUT_S, max(12, timeout_s // 4))
    deadline = asyncio.get_event_loop().time() + settle_timeout
    last_text = ""
    last_key = ""
    stable_samples = 0

    while asyncio.get_event_loop().time() < deadline:
        await _scroll_last_assistant_into_view(page)
        text = await _last_assistant_text(page)
        key = _THINKING_TIMER_RE.sub("thought for", text)

        if text:
            stable_samples = stable_samples + 1 if key == last_key else 1
        else:
            stable_samples = 0

        if stable_samples >= TEXT_STABLE_SAMPLES:
            return text

        last_text = text or last_text
        last_key = key
        await asyncio.sleep(TEXT_STABLE_INTERVAL_S)

    if not last_text:
        raise ChatGPTResponseError("Có câu trả lời mới nhưng nội dung rỗng.")
    if require_stable:
        raise ChatGPTResponseError(
            f"Nút dừng không tắt và câu trả lời cũng chưa ổn định sau {settle_timeout}s."
        )

    log.warning("Câu trả lời chưa ổn định sau %ss, lấy bản mới nhất (%d ký tự)", settle_timeout, len(last_text))
    return last_text


async def ensure_instant_mode(page) -> None:
    """Tự động chuyển chế độ ChatGPT sang 'Instant' (tắt Thinking/Reasoning effort).

    Trên tài khoản ChatGPT Plus, OpenAI hay mặc định 'Thinking effort' khiến mỗi
    batch dịch sinh ra nhiều reasoning token và kéo dài 1-2 phút thay vì 5-15s.
    Hàm này tìm nút switcher trên composer: nếu đã ở 'Instant' thì return ngay;
    nếu đang ở 'Thinking effort' thì bấm mở popover và chọn 'Instant'.

    Nuốt ngoại lệ một cách an toàn (best-effort): nếu OpenAI đổi selector thì log
    warning chứ không làm crash job dịch.
    """
    try:
        switcher = None
        for sel in MODEL_SWITCHER_SELS:
            try:
                loc = page.locator(sel).first
                if await loc.is_visible(timeout=1_000):
                    switcher = loc
                    break
            except Exception:
                continue

        if switcher is None:
            return

        label = (await switcher.inner_text(timeout=1_000)).strip()
        if "instant" in label.lower():
            return

        log.info("Phát hiện ChatGPT đang ở chế độ '%s' — tự động chuyển sang 'Instant'", label)
        await switcher.click()
        await asyncio.sleep(0.4)

        picked = False
        for sel in INSTANT_MENU_ITEM_SELS:
            try:
                item = page.locator(sel).first
                if await item.is_visible(timeout=1_500):
                    await item.click()
                    picked = True
                    break
            except Exception:
                continue

        if not picked:
            try:
                slider = page.locator('[role="slider"], input[type="range"]').first
                if await slider.is_visible(timeout=1_000):
                    await slider.focus()
                    await page.keyboard.press("Home")
                    await page.keyboard.press("ArrowLeft")
                    picked = True
            except Exception:
                pass

        if not picked:
            try:
                opt = page.get_by_text(re.compile(r"^Instant", re.I)).first
                if await opt.is_visible(timeout=1_000):
                    await opt.click()
                    picked = True
            except Exception:
                pass

        await asyncio.sleep(0.3)

        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass

        if picked:
            log.info("Đã chuyển thành công sang chế độ Instant")
        else:
            log.warning("Không tìm thấy tùy chọn 'Instant' trong menu")
    except Exception as exc:  # noqa: BLE001
        log.warning("Không thể tự chuyển sang Instant mode: %s", exc)


async def _enter_prompt(page, input_el, prompt: str) -> None:
    # 1. Dismiss any overlay popups/dialogs if present
    for sel in (
        'button:has-text("Stay logged out")',
        'button:has-text("Dismiss")',
        'button:has-text("Close")',
        'button:has-text("Not now")',
        'button:has-text("Done")',
        'button:has-text("Okay")',
        'button:has-text("Reject non-essential")',
        'button:has-text("Accept all")',
        'button[aria-label="Close"]',
    ):
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=300):
                await btn.click()
                await asyncio.sleep(0.2)
        except Exception:
            pass

    # 2. Focus input
    try:
        await input_el.focus()
    except Exception:
        pass
    await asyncio.sleep(0.1)

    # 3. Fast DOM insertion via execCommand & input events (instant for any length)
    try:
        inserted = await page.evaluate(
            """([sel, text]) => {
                const el = document.querySelector(sel);
                if (!el) return false;
                el.focus();
                if (el.isContentEditable) {
                    el.innerText = '';
                    const ok = document.execCommand('insertText', false, text);
                    if (!ok || !el.innerText.trim()) {
                        el.innerText = text;
                    }
                    el.dispatchEvent(new InputEvent('input', { bubbles: true, cancelable: true, inputType: 'insertText', data: text }));
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                } else if ('value' in el) {
                    el.value = text;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                return false;
            }""",
            [PROMPT_INPUT_SEL, prompt],
        )
        if inserted:
            await asyncio.sleep(0.2)
            val = await input_el.evaluate("(el) => (el.innerText || el.value || '').trim()")
            if val:
                return
    except Exception as exc:
        log.warning("DOM execCommand thất bại (%s), thử fallback", exc)

    # 4. Fallback fill / keyboard
    try:
        await input_el.fill(prompt, timeout=2_000)
    except Exception:
        await page.keyboard.insert_text(prompt)
    await asyncio.sleep(0.2)


async def send_prompt(prompt: str, page, timeout_s: int, ensure_instant: bool = False) -> str:
    """Send one message on an already-open conversation and return the reply."""
    if not prompt.strip():
        raise ChatGPTResponseError("Prompt rỗng.")

    prev_count = await page.locator(ASSISTANT_MSG_SEL).count()

    input_el = page.locator(PROMPT_INPUT_SEL).first
    try:
        await input_el.wait_for(state="visible", timeout=10_000)
    except Exception as exc:  # noqa: BLE001
        raise ChatGPTResponseError(f"Không thấy ô nhập chat: {exc}") from exc

    if ensure_instant and prev_count == 0:
        await ensure_instant_mode(page)

    log.info("Gửi prompt (%d ký tự)", len(prompt))
    try:
        await _enter_prompt(page, input_el, prompt)
        await asyncio.sleep(0.3)
        await _click_send(page)
        await asyncio.sleep(1)
    except Exception as exc:
        raise ChatGPTResponseError(f"Lỗi khi nhập hoặc gửi prompt: {exc}") from exc

    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if await page.locator(ASSISTANT_MSG_SEL).count() > prev_count:
            break
        await asyncio.sleep(POLL_INTERVAL_S)
    else:
        await _raise_if_rate_limited(page)
        raise ChatGPTResponseError(f"Không có câu trả lời nào trong {timeout_s}s.")

    # A stop button that never goes away is usually a stuck UI, not a still-running
    # answer — observed once as a 300s hang on a prompt that normally takes 15s.
    # Throwing the turn away costs a full re-send, so check whether the text settled
    # regardless before giving up on it.
    stuck = False
    try:
        await _wait_streaming_done(page, timeout_s)
    except ChatGPTResponseError:
        log.warning("Nút dừng chưa tắt sau %ss — kiểm tra xem câu trả lời có ổn định không", timeout_s)
        stuck = True

    text = await _wait_text_stable(page, timeout_s, require_stable=stuck)

    # The quota message also arrives as an ordinary assistant turn. Left alone it
    # would be handed to the caller as the answer, and S2 would quietly fall back
    # to rule-based breaks with only a warning in the log to show for it.
    fragment = find_limit_fragment(text)
    if fragment:
        raise ChatGPTRateLimitError(f"ChatGPT hết hạn mức tin nhắn ({fragment!r}): {text[:200]}")

    fragment = find_refusal_fragment(text)
    if fragment:
        raise ChatGPTContentRefusal(f"ChatGPT chặn nội dung ({fragment!r}): {text[:160]}")

    log.info("Nhận câu trả lời (%d ký tự)", len(text))
    return text


async def ask(
    page,
    prompt: str,
    timeout_s: int,
    conversation_url: str | None,
    ensure_instant: bool = True,
) -> tuple[str, str | None]:
    """Send one turn and return ``(answer, conversation_url)``.

    Passing back the URL is what lets a caller keep every turn of one stage in a
    single thread: S4's later batches can then see the wording the model already
    chose, so pronouns and register stay consistent across the whole video instead
    of being re-decided from scratch every 60 lines.

    The tab is shared, so the conversation is re-opened by URL on every turn rather
    than assumed to still be on screen — with ``batch -j 4`` another job's stage
    will have navigated away in between.
    """
    await page.goto(
        conversation_url or NEW_CHAT_URL, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS
    )
    if ensure_instant and conversation_url is None:
        await ensure_instant_mode(page)
    text = await send_prompt(prompt, page, timeout_s, ensure_instant=ensure_instant)
    url = page.url if page.url.startswith(CONVERSATION_URL_PREFIX) else conversation_url
    return text, url
