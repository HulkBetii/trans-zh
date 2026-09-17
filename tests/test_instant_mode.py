"""Tests for ensure_instant_mode in chatgpt_web."""

from __future__ import annotations

import asyncio
import re
import pytest

from zhsub.llm.chatgpt_web import chat


class MockLocator:
    def __init__(self, page: MockPage, selector: str, text: str = "", visible: bool = True):
        self.page = page
        self.selector = selector
        self._text = text
        self._visible = visible

    @property
    def first(self):
        return self

    def nth(self, n: int):
        return self

    async def is_visible(self, **kwargs) -> bool:
        return self._visible

    async def is_enabled(self, **kwargs) -> bool:
        return True

    async def inner_text(self, **kwargs) -> str:
        return self._text

    async def count(self) -> int:
        return 1 if self._visible else 0

    async def click(self, **kwargs) -> None:
        self.page.clicked.append(self.selector)

    async def fill(self, value: str) -> None:
        self.page.filled.append(value)

    async def wait_for(self, **kwargs) -> None:
        pass

    async def focus(self, **kwargs) -> None:
        pass

    async def evaluate(self, script: str, **kwargs) -> str:
        return self._text


class MockKeyboard:
    def __init__(self, page: MockPage):
        self.page = page

    async def press(self, key: str) -> None:
        self.page.keys_pressed.append(key)


class MockPage:
    def __init__(self, switcher_text: str = "Instant", switcher_visible: bool = True):
        self.switcher_text = switcher_text
        self.switcher_visible = switcher_visible
        self.clicked: list[str] = []
        self.filled: list[str] = []
        self.keys_pressed: list[str] = []
        self.keyboard = MockKeyboard(self)
        self.url = chat.NEW_CHAT_URL

    def locator(self, selector: str):
        if any(s in selector for s in ('"Instant"', 'Instant')):
            if "button:has-text" in selector:
                is_inst = "instant" in self.switcher_text.lower()
                return MockLocator(self, selector, text=self.switcher_text, visible=self.switcher_visible and is_inst)
            return MockLocator(self, selector, text="Instant", visible=True)
        if any(s in selector for s in ('"Thinking effort"', '"Thinking"')):
            is_thinking = "thinking" in self.switcher_text.lower()
            return MockLocator(self, selector, text=self.switcher_text, visible=self.switcher_visible and is_thinking)
        if selector == chat.PROMPT_INPUT_SEL:
            return MockLocator(self, selector, visible=True)
        if selector in chat.SEND_BUTTON_SELS:
            return MockLocator(self, selector, visible=True)
        if selector == chat.ASSISTANT_MSG_SEL:
            return MockLocator(self, selector, text="Response", visible=True)
        if selector == chat.STOP_BUTTON_SEL:
            return MockLocator(self, selector, visible=False)
        return MockLocator(self, selector, text="", visible=False)

    def get_by_text(self, text_or_regex):
        return MockLocator(self, str(text_or_regex), text="Instant", visible=True)

    async def goto(self, url: str, **kwargs):
        self.url = url


def test_ensure_instant_mode_when_already_instant():
    page = MockPage(switcher_text="Instant", switcher_visible=True)
    asyncio.run(chat.ensure_instant_mode(page))
    assert len(page.clicked) == 0


def test_ensure_instant_mode_switches_from_thinking():
    page = MockPage(switcher_text="Thinking effort", switcher_visible=True)
    asyncio.run(chat.ensure_instant_mode(page))
    assert len(page.clicked) >= 2
    assert any("Thinking" in c for c in page.clicked)
    assert any("Instant" in c for c in page.clicked)
    assert "Escape" in page.keys_pressed


def test_ensure_instant_mode_graceful_when_no_switcher():
    page = MockPage(switcher_text="", switcher_visible=False)
    asyncio.run(chat.ensure_instant_mode(page))
    assert len(page.clicked) == 0
