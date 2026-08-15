"""Provider chatgpt_web — phần chạy được mà không cần trình duyệt."""

from __future__ import annotations

import asyncio
import threading
import time
import tomllib
from contextlib import contextmanager
from pathlib import Path

import pytest

from zhsub.config import ChatGPTWebConfig, Config
from zhsub.llm.base import LLMError
from zhsub.llm.chatgpt_web import ChatGPTWebProvider, compose_prompt
from zhsub.llm.chatgpt_web import chat, session as session_mod
from zhsub.llm.factory import build_provider

CONFIG_TOML = """
[llm.segment]
provider = "chatgpt_web"
model = "chatgpt-web"
timeout_sec = 300

[llm.translate]
provider = "chatgpt_web"
model = "chatgpt-web"
temperature = 0.3
timeout_sec = 600

[llm.chatgpt_web]
profile_dir = "data/profile-thu"
account_file = "data/tai-khoan.json"
manual_login_timeout_sec = 60
"""


def test_system_and_user_land_in_one_prompt():
    """Ô nhập của ChatGPT chỉ có một, không có system role để tách ra."""
    prompt = compose_prompt("SYSTEM PART", "USER PART", json_mode=False)

    assert prompt.index("SYSTEM PART") < prompt.index("USER PART")
    assert "\n" in prompt[len("SYSTEM PART") : prompt.index("USER PART")]


def test_json_mode_appends_an_instruction():
    plain = compose_prompt("s", "u", json_mode=False)
    as_json = compose_prompt("s", "u", json_mode=True)

    assert as_json.startswith(plain)
    assert "JSON" in as_json[len(plain) :]


def test_empty_system_leaves_no_dangling_separator():
    assert compose_prompt("", "USER PART", json_mode=False) == "USER PART"


def test_config_selects_the_browser_provider_for_both_stages():
    cfg = Config.model_validate(tomllib.loads(CONFIG_TOML))

    assert cfg.llm.chatgpt_web.profile_dir == "data/profile-thu"
    assert cfg.llm.chatgpt_web.manual_login_timeout_sec == 60
    for who in ("segment", "translate"):
        provider = build_provider(cfg.llm, who)
        assert isinstance(provider, ChatGPTWebProvider)


def test_provider_carries_the_profile_timeout_not_the_default():
    """timeout_sec của profile là thứ giới hạn thời gian chờ ChatGPT trả lời."""
    cfg = Config.model_validate(tomllib.loads(CONFIG_TOML))

    assert build_provider(cfg.llm, "translate").timeout_sec == 600


class FakeLocator:
    """Đủ bề mặt Playwright cho chat.py, không hơn."""

    def __init__(self, page: FakePage, selector: str, index: int = 0) -> None:
        self.page = page
        self.selector = selector
        self.index = index

    @property
    def first(self) -> FakeLocator:
        return self

    def nth(self, index: int) -> FakeLocator:
        return FakeLocator(self.page, self.selector, index)

    async def count(self) -> int:
        # Nút stop luôn vắng mặt: mọi selector khác đếm bằng 0 nên
        # _wait_streaming_done trả về ngay.
        return len(self.page.messages) if self.selector == chat.ASSISTANT_MSG_SEL else 0

    async def evaluate(self, script: str, **kwargs) -> str:
        return self.page.messages[self.index]

    async def inner_text(self, **kwargs) -> str:
        return "\n".join(self.page.messages)

    async def click(self, **kwargs) -> None:
        if self.selector in chat.SEND_BUTTON_SELS:
            self.page.send()

    async def fill(self, value: str) -> None:
        self.page.prompts.append(value)

    async def wait_for(self, **kwargs) -> None: ...
    async def press(self, key: str) -> None: ...
    async def scroll_into_view_if_needed(self, **kwargs) -> None: ...
    async def is_visible(self, **kwargs) -> bool:
        return True

    async def is_enabled(self, **kwargs) -> bool:
        return True


FAKE_CONVERSATION_URL = chat.CONVERSATION_URL_PREFIX + "abc-123"


class FakePage:
    def __init__(self, reply: str | None) -> None:
        self.reply = reply
        self.messages: list[str] = []
        self.prompts: list[str] = []
        self.visited: list[str] = []
        self.url = chat.NEW_CHAT_URL
        self.closed = False
        self.conversation_urls = iter(())

    def is_closed(self) -> bool:
        return self.closed

    @property
    def filled(self) -> str:
        return self.prompts[-1] if self.prompts else ""

    def send(self) -> None:
        if self.reply is None:
            return
        self.messages.append(self.reply)
        # Như thật: chat mới chỉ có URL hội thoại sau khi tin nhắn đầu tiên gửi đi.
        if self.url == chat.NEW_CHAT_URL:
            self.url = next(self.conversation_urls, FAKE_CONVERSATION_URL)

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def goto(self, url: str, **kwargs) -> None:
        self.visited.append(url)
        self.url = url

    async def evaluate(self, script: str) -> None: ...


@pytest.fixture
def instant_polling(monkeypatch):
    monkeypatch.setattr(chat, "POLL_INTERVAL_S", 0)
    monkeypatch.setattr(chat, "TEXT_STABLE_INTERVAL_S", 0)


def test_send_prompt_types_the_prompt_and_returns_the_reply(instant_polling):
    page = FakePage(reply='{"translations": []}')

    text = asyncio.run(chat.send_prompt("DỊCH ĐI", page, timeout_s=5))

    assert text == '{"translations": []}'
    assert page.filled == "DỊCH ĐI"


def test_ask_opens_a_new_chat_and_reports_the_conversation_url(instant_polling):
    page = FakePage(reply="xong")

    text, url = asyncio.run(chat.ask(page, "prompt", timeout_s=5, conversation_url=None))

    assert text == "xong"
    assert page.visited == [chat.NEW_CHAT_URL]
    assert url == FAKE_CONVERSATION_URL


def test_ask_reopens_the_conversation_it_is_given(instant_polling):
    """Tab dùng chung, nên phải mở lại hội thoại theo URL chứ không giả định nó
    vẫn còn trên màn hình — `batch -j 4` có job khác chen vào giữa."""
    page = FakePage(reply="xong")

    asyncio.run(chat.ask(page, "prompt", timeout_s=5, conversation_url=FAKE_CONVERSATION_URL))

    assert page.visited == [FAKE_CONVERSATION_URL]


def test_no_answer_within_the_timeout_is_an_error(instant_polling):
    page = FakePage(reply=None)

    with pytest.raises(chat.ChatGPTResponseError, match="Không có câu trả lời"):
        asyncio.run(chat.send_prompt("prompt", page, timeout_s=1))


class FakeSession:
    """Đủ để chạy provider._call mà không cần trình duyệt."""

    def __init__(self, page: FakePage) -> None:
        self._page = page

    def run(self, coro):
        return asyncio.run(coro)

    @contextmanager
    def page(self):
        yield self._page


def test_rate_limit_is_not_swallowed_by_the_retry_loop(instant_polling, monkeypatch):
    """Hết hạn mức phải dừng job ngay, không retry — thử lại chỉ tốn thêm phút.

    Điều đó chỉ đúng nếu ChatGPTRateLimitError KHÔNG bị bọc thành LLMError:
    provider._call chỉ bắt ChatGPTResponseError, còn vòng retry ở base.complete
    và mọi `except LLMError` trong các stage đều chỉ bắt LLMError.
    """
    page = FakePage(reply="You've reached your limit of messages until 3:00 PM.")
    monkeypatch.setattr("zhsub.llm.chatgpt_web.provider.get_session", lambda web: FakeSession(page))
    provider = ChatGPTWebProvider(model="chatgpt-web", web=ChatGPTWebConfig(), max_retries=3)

    with pytest.raises(chat.ChatGPTRateLimitError):
        provider.complete("system", "user")

    assert not issubclass(chat.ChatGPTRateLimitError, LLMError)
    assert not issubclass(chat.ChatGPTRateLimitError, chat.ChatGPTResponseError)
    # Một lần gửi duy nhất: retry sẽ đâm vào đúng bức tường đó.
    assert len(page.messages) == 1


def _provider_on(page: FakePage, monkeypatch) -> ChatGPTWebProvider:
    monkeypatch.setattr("zhsub.llm.chatgpt_web.provider.get_session", lambda web: FakeSession(page))
    # timeout_sec=1: nhánh "không có câu trả lời" quay đúng chừng đó giây thật.
    return ChatGPTWebProvider(model="chatgpt-web", web=ChatGPTWebConfig(), max_retries=0, timeout_sec=1)


def test_same_system_prompt_stays_in_one_conversation(instant_polling, monkeypatch):
    """Một stage = một thread, để S4 thấy được cách nó đã dịch ở batch trước.

    Ranh giới thread lấy theo system prompt: S2 dùng chung một prompt suốt, S4 sinh
    prompt riêng cho mỗi ngôn ngữ, nên không stage nào phải biết provider này tồn tại.
    """
    page = FakePage(reply="ok")
    provider = _provider_on(page, monkeypatch)

    provider.complete("RULES", "batch 1")
    provider.complete("RULES", "batch 2")
    provider.complete("RULES", "batch 3")

    # Mở chat mới đúng một lần, hai lượt sau quay lại đúng hội thoại đó.
    assert page.visited == [chat.NEW_CHAT_URL, FAKE_CONVERSATION_URL, FAKE_CONVERSATION_URL]
    # Luật lặp lại mọi lượt: đo trên clip 472 câu, nói một lần rồi tin vào thread
    # thì model trôi khỏi rule 3 và bắt đầu lẫn lộn "anh ta" với "ông ấy".
    assert all("RULES" in p for p in page.prompts)


def test_a_different_system_prompt_starts_a_new_conversation(instant_polling, monkeypatch):
    """S4 đổi ngôn ngữ là đổi system prompt — bản dịch tiếng Việt không được
    rò sang thread tiếng Anh."""
    page = FakePage(reply="ok")
    provider = _provider_on(page, monkeypatch)

    provider.complete("RULES vi", "batch 1")
    provider.complete("RULES en", "batch 1")

    assert page.visited == [chat.NEW_CHAT_URL, chat.NEW_CHAT_URL]
    assert "RULES en" in page.prompts[1]


def test_alternating_system_prompts_keep_two_conversations(instant_polling, monkeypatch):
    """S4 với review_pass đổi qua lại giữa luật thường và luật + phần rà soát ở mọi
    lần gọi. Nhớ mỗi một hội thoại gần nhất thì lượt nào cũng thành chat mới — đo
    được trên job thật: 16 lần gọi ra đúng 16 hội thoại, mất sạch continuity.
    """
    page = FakePage(reply="ok")
    provider = _provider_on(page, monkeypatch)
    page.conversation_urls = iter(
        [chat.CONVERSATION_URL_PREFIX + "nhap", chat.CONVERSATION_URL_PREFIX + "rasoat"]
    )

    provider.complete("RULES", "batch 1")           # nháp
    provider.complete("RULES + REVIEW", "batch 1")  # rà soát
    provider.complete("RULES", "batch 2")           # nháp, phải quay lại thread nháp
    provider.complete("RULES + REVIEW", "batch 2")

    assert page.visited == [
        chat.NEW_CHAT_URL,
        chat.NEW_CHAT_URL,
        chat.CONVERSATION_URL_PREFIX + "nhap",
        chat.CONVERSATION_URL_PREFIX + "rasoat",
    ]


def test_a_failed_opening_turn_does_not_claim_the_thread(instant_polling, monkeypatch):
    """Lượt mở màn hỏng mà vẫn ghi nhận thread thì lượt sau gửi vào một hội thoại
    chưa bao giờ nhận được luật."""
    page = FakePage(reply=None)  # không bao giờ trả lời
    provider = _provider_on(page, monkeypatch)

    with pytest.raises(LLMError):
        provider.complete("RULES", "batch 1")

    page.reply = "ok"
    provider.complete("RULES", "batch 2")

    assert "RULES" in page.prompts[-1]


def test_upgrade_link_in_the_sidebar_is_not_a_rate_limit():
    """Nút "Upgrade plan" nằm thường trực ở sidebar — bắt theo từ khoá cụt sẽ
    tuyên bố mọi lần gọi đều hết hạn mức."""
    assert chat.find_limit_fragment("Upgrade plan\nUpgrade to Go\nNew chat") is None
    assert chat.find_limit_fragment("You've reached your limit of GPT-5 messages") is not None


def test_a_slow_answer_does_not_discard_a_live_tab():
    """Chromium tắt hẳn khi page cuối cùng của persistent context đóng lại.

    Đo được trên job thật: một lần trả lời treo 300s khiến tab bị đóng, browser tắt
    theo, và cả ba job song song chết với "browser has been closed". Trả lời chậm
    không có nghĩa là tab hỏng.
    """
    sess = session_mod.BrowserSession(Path("profile"), Path("account.json"), 1.0)
    page = FakePage(reply="ok")
    sess._page = page

    with pytest.raises(RuntimeError):
        with sess.page():
            raise RuntimeError("trả lời chậm")

    assert sess._page is page


def test_a_dead_tab_is_replaced():
    sess = session_mod.BrowserSession(Path("profile"), Path("account.json"), 1.0)
    page = FakePage(reply="ok")
    sess._page = page

    with pytest.raises(RuntimeError):
        with sess.page():
            page.closed = True
            raise RuntimeError("renderer chết")

    assert sess._page is None


def test_a_disconnected_browser_is_marked_for_restart():
    sess = session_mod.BrowserSession(Path("profile"), Path("account.json"), 1.0)
    sess._page = FakePage(reply="ok")

    with pytest.raises(session_mod.BrowserSessionUnavailable):
        with sess.page():
            raise RuntimeError("Target page, context or browser has been closed")

    assert sess._page is None


def test_page_serialises_concurrent_callers():
    """batch -j 4 không được cho 4 luồng cùng gõ vào một ô nhập."""
    sess = session_mod.BrowserSession(Path("profile"), Path("account.json"), 1.0)
    sess._page = FakePage(reply="ok")  # bỏ qua bước mở trình duyệt

    inside: list[int] = []
    peak: list[int] = []

    def worker() -> None:
        with sess.page():
            inside.append(1)
            peak.append(len(inside))
            time.sleep(0.05)
            inside.pop()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(peak) == 4
    assert max(peak) == 1


class SessionStub:
    instances: list[SessionStub] = []

    def __init__(self, profile_dir: Path, account_file: Path, manual_timeout: float) -> None:
        self.profile_dir = profile_dir
        self.account_file = account_file
        self.manual_timeout = manual_timeout
        self.alive = False
        self.started = False
        self.stopped = False
        self.instances.append(self)

    def start(self) -> None:
        self.started = True
        self.alive = True

    def stop(self) -> None:
        self.stopped = True
        self.alive = False

    def is_alive(self) -> bool:
        return self.alive


def test_get_session_starts_browser_on_first_llm_call(tmp_path, monkeypatch):
    SessionStub.instances = []
    monkeypatch.setattr(session_mod, "_sessions", {})
    monkeypatch.setattr(session_mod, "BrowserSession", SessionStub)
    cfg = ChatGPTWebConfig(
        profile_dir=str(tmp_path / "profile"),
        account_file=str(tmp_path / "account.json"),
    )

    first = session_mod.get_session(cfg)
    second = session_mod.get_session(cfg)

    assert first is second
    assert first.started is True
    assert len(SessionStub.instances) == 1


def test_get_session_restarts_a_closed_browser(tmp_path, monkeypatch):
    SessionStub.instances = []
    monkeypatch.setattr(session_mod, "_sessions", {})
    monkeypatch.setattr(session_mod, "BrowserSession", SessionStub)
    cfg = ChatGPTWebConfig(profile_dir=str(tmp_path / "profile"))

    previous = session_mod.get_session(cfg)
    previous.alive = False
    restarted = session_mod.get_session(cfg)

    assert restarted is not previous
    assert restarted.started is True
    assert previous.stopped is True
    assert len(SessionStub.instances) == 2


def test_provider_retries_after_playwright_stops(instant_polling, monkeypatch):
    class ClosedSession:
        @contextmanager
        def page(self):
            raise session_mod.BrowserSessionUnavailable("browser closed")
            yield

    page = FakePage(reply="ok")
    sessions = iter([ClosedSession(), FakeSession(page)])
    monkeypatch.setattr("zhsub.llm.chatgpt_web.provider.get_session", lambda web: next(sessions))
    monkeypatch.setattr("zhsub.llm.base.time.sleep", lambda delay: None)
    provider = ChatGPTWebProvider(
        model="chatgpt-web", web=ChatGPTWebConfig(), max_retries=1, timeout_sec=1
    )

    assert provider.complete("RULES", "batch 1") == "ok"
    assert len(page.messages) == 1
