"""TOML-backed configuration.

The model used for re-segmentation (S2) and the model used for translation (S4)
are configured separately: S2 is mechanical work where a cheap model suffices,
only S4 needs a strong one.
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

DEFAULT_CONFIG_NAMES = ("zhsub.toml", "zhsub.toml.example")
_PLACEHOLDER = "SET_ME"


class PathsConfig(BaseModel):
    work_dir: str = "work"
    cache_dir: str = ".cache"
    jobs_db: str = "jobs.db"


class IngestConfig(BaseModel):
    sample_rate: int = 16000
    channels: int = 1
    cookies_from_browser: str = ""


class AsrConfig(BaseModel):
    engine: str = "funasr-paraformer"
    model: str = "paraformer-zh"
    vad_model: str = "fsmn-vad"
    punc_model: str = "ct-punc"
    device: Literal["auto", "cuda", "cpu"] = "auto"
    batch_size_s: int = 300
    outer_chunk_threshold_sec: float = 5400.0
    outer_chunk_sec: float = 1800.0
    # 8s, not 2s. The overlap has to be wide enough to contain a whole sentence, or
    # an utterance straddling the cut is truncated in both chunks and its tokens are
    # simply lost. Measured on the 35-minute clip with the threshold forced down to
    # 10 minutes: a 2s overlap dropped 113 of 9179 tokens (1.2%) across three
    # boundaries. Cues run 0.8-7s, so 8s covers essentially all of them; the extra
    # duplication costs nothing because merge_outputs discards it on time.
    outer_chunk_overlap_sec: float = 8.0

    def resolve_device(self) -> str:
        """``auto`` picks cuda when available. ``ZHSUB_DEVICE`` overrides everything."""
        forced = os.environ.get("ZHSUB_DEVICE", "").strip().lower()
        want = forced or self.device
        if want == "cpu":
            return "cpu"
        if want == "cuda":
            return "cuda"
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except Exception:
            return "cpu"


class SegmentConfig(BaseModel):
    min_duration_sec: float = 0.8
    max_duration_sec: float = 7.0
    merge_gap_sec: float = 0.25
    # The stream sent to the LLM is split at VAD silences longer than this.
    chunk_at_silence_sec: float = 0.5
    max_chars_per_llm_call: int = 1800
    # Below this match ratio the response is treated as unusable rather than repaired.
    repair_min_ratio: float = 0.95
    max_retries: int = 2


class TranslateConfig(BaseModel):
    batch_size: int = 40
    context_before: int = 10
    context_after: int = 5
    review_pass: bool = True
    max_retries: int = 3
    # MUST be bumped whenever a prompt changes, otherwise stale cache entries are
    # served — silently, which is the worst kind of wrong.
    #   v2: prompts and JSON keys moved to English, and scene context moved out of
    #       the JSON payload, after both were measured to break translation on
    #       small local models.
    #   v3: per-entry "max_chars" budget added to the prompt.
    #   v4: max_lines 2 -> 3 changed every "max_chars" value.
    #   v5: address terms scoped to direct speech only — as a blanket rule they
    #       flipped narration into the first person.
    #   v6: preserving grammatical person promoted to a top-level rule; inside the
    #       address-terms block it only applied when a glossary happened to have one.
    #   v7: idiom-force and register rules. Measured on 15 idiom-bearing lines: on
    #       gpt-4o-mini these rules changed almost nothing (mean length 94.5 -> 95.5,
    #       same 4/15 over budget). The same prompt on gpt-5 gave 81.5 chars and real
    #       idiomatic renderings. The binding constraint here is the model, not the
    #       prompt — worth remembering before writing more prompt text.
    prompt_version: int = 7


class LangLimits(BaseModel):
    max_chars_per_line: int = 42
    max_cps: float = 21.0


class RenderConfig(BaseModel):
    max_lines: int = 2
    # 42 chars / 21 CPS is a Latin-script convention. Chinese lines need to be far
    # tighter (CJK convention is ~20 chars / 9 CPS) or they overflow in bilingual mode.
    limits: dict[str, LangLimits] = Field(
        default_factory=lambda: {
            "vi": LangLimits(),
            "en": LangLimits(),
            "zh": LangLimits(max_chars_per_line=20, max_cps=9.0),
        }
    )

    def for_lang(self, lang: str) -> LangLimits:
        return self.limits.get(lang, LangLimits())


class LLMProfile(BaseModel):
    provider: Literal["openai", "anthropic"] = "openai"
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    model: str = _PLACEHOLDER
    temperature: float = 0.0
    max_retries: int = 3
    timeout_sec: float = 120.0

    def api_key(self) -> str:
        """Resolve the key from the environment.

        An empty ``api_key_env`` means the endpoint needs no auth — Ollama, LM
        Studio and vLLM all ignore the header. They do still reject a *missing*
        Authorization header on some builds, so a placeholder goes out instead.
        """
        if not self.api_key_env:
            return "local"
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise RuntimeError(
                f"Chưa có API key: biến môi trường {self.api_key_env} trống. "
                f"Đặt nó rồi chạy lại, hoặc để api_key_env = \"\" nếu endpoint chạy local."
            )
        return key

    def require_model(self, who: str) -> str:
        if not self.model or self.model == _PLACEHOLDER:
            raise RuntimeError(
                f"Chưa đặt model cho [llm.{who}] trong zhsub.toml (đang là {_PLACEHOLDER!r})."
            )
        return self.model


class LLMConfig(BaseModel):
    segment: LLMProfile = Field(default_factory=LLMProfile)
    translate: LLMProfile = Field(
        default_factory=lambda: LLMProfile(temperature=0.3, timeout_sec=180.0)
    )


class Config(BaseModel):
    paths: PathsConfig = Field(default_factory=PathsConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    asr: AsrConfig = Field(default_factory=AsrConfig)
    segment: SegmentConfig = Field(default_factory=SegmentConfig)
    translate: TranslateConfig = Field(default_factory=TranslateConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)

    @classmethod
    def load(cls, path: str | Path | None = None) -> Config:
        """Load config, falling back to ``zhsub.toml`` then the example file.

        Finding neither is fine — defaults are usable, which matters for the
        benchmark stage since it never touches an LLM.
        """
        if path is not None:
            return cls._from_file(Path(path))
        for name in DEFAULT_CONFIG_NAMES:
            candidate = Path.cwd() / name
            if candidate.is_file():
                return cls._from_file(candidate)
        return cls()

    @classmethod
    def _from_file(cls, path: Path) -> Config:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        return cls.model_validate(raw)
