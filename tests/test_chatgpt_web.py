"""Provider chatgpt_web — phần chạy được mà không cần trình duyệt."""

from __future__ import annotations

import asyncio
import tomllib

import pytest

from zhsub.config import Config
from zhsub.llm.chatgpt_web import ChatGPTWebProvider, compose_prompt
from zhsub.llm.chatgpt_web import chat
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

    async def click(self, **kwargs) -> None:
        if self.selector in chat.SEND_BUTTON_SELS:
            self.page.send()

    async def fill(self, value: str) -> None:
        self.page.filled = value

    async def wait_for(self, **kwargs) -> None: ...
    async def press(self, key: str) -> None: ...
    async def scroll_into_view_if_needed(self, **kwargs) -> None: ...
    async def is_visible(self, **kwargs) -> bool:
        return True

    async def is_enabled(self, **kwargs) -> bool:
        return True


class FakePage:
    def __init__(self, reply: str | None) -> None:
        self.reply = reply
        self.messages: list[str] = []
        self.filled = ""
        self.visited: list[str] = []

    def send(self) -> None:
        if self.reply is not None:
            self.messages.append(self.reply)

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def goto(self, url: str, **kwargs) -> None:
        self.visited.append(url)

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


def test_ask_opens_a_fresh_conversation_before_sending(instant_polling):
    """Dùng lại một thread thì batch sau bị nhiễm id của batch trước."""
    page = FakePage(reply="xong")

    asyncio.run(chat.ask(page, "prompt", timeout_s=5))

    assert page.visited == [chat.NEW_CHAT_URL]


def test_no_answer_within_the_timeout_is_an_error(instant_polling):
    page = FakePage(reply=None)

    with pytest.raises(chat.ChatGPTResponseError, match="Không có câu trả lời"):
        asyncio.run(chat.send_prompt("prompt", page, timeout_s=1))
