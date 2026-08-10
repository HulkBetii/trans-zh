"""Build a provider from a config profile."""

from __future__ import annotations

from typing import Literal

from ..config import LLMConfig
from .anthropic import AnthropicProvider
from .base import LLMProvider


def build_provider(llm: LLMConfig, who: Literal["segment", "translate"]) -> LLMProvider:
    """``who`` picks the profile and doubles as the label in error messages.

    The whole :class:`LLMConfig` is passed, not just the profile, because the
    browser-backed provider reads settings shared across profiles.
    """
    profile = getattr(llm, who)
    model = profile.require_model(who)

    if profile.provider == "chatgpt_web":
        from .chatgpt_web import ChatGPTWebProvider

        return ChatGPTWebProvider(
            model=model,
            web=llm.chatgpt_web,
            temperature=profile.temperature,
            max_retries=profile.max_retries,
            timeout_sec=profile.timeout_sec,
        )

    if profile.provider == "anthropic":
        return AnthropicProvider(
            model=model,
            base_url=profile.base_url,
            api_key=profile.api_key(),
            temperature=profile.temperature,
            max_retries=profile.max_retries,
            timeout_sec=profile.timeout_sec,
        )

    from .openai_compat import OpenAICompatProvider

    return OpenAICompatProvider(
        model=model,
        base_url=profile.base_url,
        api_key=profile.api_key(),
        temperature=profile.temperature,
        max_retries=profile.max_retries,
        timeout_sec=profile.timeout_sec,
    )
