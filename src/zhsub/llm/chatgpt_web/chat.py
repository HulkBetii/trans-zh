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

# Temporary chat: one throwaway conversation per call. Without it a job leaves
# hundreds of one-shot conversations in the account's history.
NEW_CHAT_URL = "https://chatgpt.com/?temporary-chat=true"

PROMPT_INPUT_SEL = "#prompt-textarea"
SEND_BUTTON_SELS = (
    'button[data-testid="send-button"]',
    'button[data-testid="composer-send-button"]',
    'button[aria-label*="Send"]',
)
ASSISTANT_MSG_SEL = '[data-message-author-role="assistant"]'
STOP_BUTTON_SEL = 'button[data-testid="stop-button"], button[aria-label="Stop generating"]'

POLL_INTERVAL_S = 2
TEXT_STABLE_SAMPLES = 3
TEXT_STABLE_INTERVAL_S = 2
TEXT_SETTLE_TIMEOUT_S = 45

# "Thought for 12s" ticks up on its own while the answer below it is already final.
_THINKING_TIMER_RE = re.compile(r"\bthought for \d+\s*(?:s|sec|seconds)\b", re.I)


class ChatGPTResponseError(RuntimeError):
    """Sending the prompt or reading the answer failed. Retryable."""


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
    for sel in SEND_BUTTON_SELS:
        try:
            btn = page.locator(sel).first
            if await btn.is_visible(timeout=2_000) and await btn.is_enabled():
                await btn.click()
                return
        except Exception:  # noqa: BLE001 - try the next selector
            continue
    log.warning("Không thấy nút gửi, dùng phím Enter thay thế")
    await page.locator(PROMPT_INPUT_SEL).first.press("Enter")


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


async def _wait_text_stable(page, timeout_s: int) -> str:
    """Poll the last answer until it stops changing across consecutive samples."""
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

    log.warning("Câu trả lời chưa ổn định sau %ss, lấy bản mới nhất (%d ký tự)", settle_timeout, len(last_text))
    return last_text


async def send_prompt(prompt: str, page, timeout_s: int) -> str:
    """Send one message on an already-open conversation and return the reply."""
    if not prompt.strip():
        raise ChatGPTResponseError("Prompt rỗng.")

    prev_count = await page.locator(ASSISTANT_MSG_SEL).count()

    input_el = page.locator(PROMPT_INPUT_SEL).first
    try:
        await input_el.wait_for(state="visible", timeout=10_000)
    except Exception as exc:  # noqa: BLE001
        raise ChatGPTResponseError(f"Không thấy ô nhập chat: {exc}") from exc

    log.info("Gửi prompt (%d ký tự)", len(prompt))
    await input_el.click()
    await asyncio.sleep(0.2)
    await input_el.fill(prompt)
    await asyncio.sleep(0.3)
    await _click_send(page)
    await asyncio.sleep(1)

    deadline = asyncio.get_event_loop().time() + timeout_s
    while asyncio.get_event_loop().time() < deadline:
        if await page.locator(ASSISTANT_MSG_SEL).count() > prev_count:
            break
        await asyncio.sleep(POLL_INTERVAL_S)
    else:
        raise ChatGPTResponseError(f"Không có câu trả lời nào trong {timeout_s}s.")

    await _wait_streaming_done(page, timeout_s)
    text = await _wait_text_stable(page, timeout_s)
    log.info("Nhận câu trả lời (%d ký tự)", len(text))
    return text


async def ask(page, prompt: str, timeout_s: int) -> str:
    """Ask one question in a brand-new conversation.

    A fresh chat per call is not optional. The pipeline sends independent batches
    that share a system prompt; kept in one thread, ChatGPT starts answering with
    ids from the previous batch, which JSON parsing accepts and the pipeline then
    happily writes into the wrong cues.
    """
    await page.goto(NEW_CHAT_URL, wait_until="domcontentloaded", timeout=NAVIGATION_TIMEOUT_MS)
    return await send_prompt(prompt, page, timeout_s)
