"""Config đọc từ TOML.

Model dùng để ngắt câu (S2) và model dùng để dịch (S4) cấu hình tách rời: S2 là
việc cơ học nên model rẻ là đủ, S4 mới cần model mạnh.
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
    outer_chunk_overlap_sec: float = 2.0

    def resolve_device(self) -> str:
        """``auto`` -> cuda nếu có, ngược lại cpu. ``ZHSUB_DEVICE`` ghi đè tất cả."""
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
    chunk_at_silence_sec: float = 0.5
    max_chars_per_llm_call: int = 1800
    repair_min_ratio: float = 0.95
    max_retries: int = 2


class TranslateConfig(BaseModel):
    batch_size: int = 40
    context_before: int = 10
    context_after: int = 5
    review_pass: bool = True
    max_retries: int = 3
    prompt_version: int = 1


class LangLimits(BaseModel):
    max_chars_per_line: int = 42
    max_cps: float = 21.0


class RenderConfig(BaseModel):
    max_lines: int = 2
    # 42 ký tự / CPS 21 là chuẩn chữ Latin. Dòng tiếng Trung phải chặt hơn
    # nhiều (chuẩn CJK ~20 ký tự / CPS 9), nếu không sẽ tràn ở chế độ bilingual.
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
        key = os.environ.get(self.api_key_env, "").strip()
        if not key:
            raise RuntimeError(
                f"Chưa có API key: biến môi trường {self.api_key_env} trống. "
                f"Đặt nó rồi chạy lại."
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
        """Đọc config. Không truyền path thì tìm ``zhsub.toml`` rồi tới file example.

        Không tìm thấy gì cả vẫn chạy được bằng giá trị mặc định — cần thiết cho
        giai đoạn benchmark, vốn không đụng tới LLM.
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
