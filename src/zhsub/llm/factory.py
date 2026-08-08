"""Build a provider from a config profile."""

from __future__ import annotations

from ..config import LLMProfile
from .anthropic import AnthropicProvider
from .base import LLMProvider


def build_provider(profile: LLMProfile, who: str) -> LLMProvider:
    """``who`` is the profile name ("segment" / "translate"), used in error messages."""
    model = profile.require_model(who)

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
