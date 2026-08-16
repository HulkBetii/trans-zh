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

from pydantic import BaseModel, Field, PrivateAttr

from .credentials import (
    CredentialStore,
    CredentialStoreError,
    get_default_credential_store,
    resolve_secret,
)

DEFAULT_CONFIG_NAMES = ("zhsub.toml", "zhsub.toml.example")
_PLACEHOLDER = "SET_ME"


class PathsConfig(BaseModel):
    work_dir: str = "work"
    cache_dir: str = ".cache"
    jobs_db: str = "jobs.db"

    # Nơi Studio ghi phụ đề và audio đã xuất. Trước đây hằng số "output" nằm cứng
    # trong server, tính tương đối với cwd — nghĩa là một app đã cài sẽ ghi ra
    # bất kỳ đâu mà shortcut trỏ tới, và bộ test ghi thẳng vào output/ của repo.
    #
    # Job cũ không có request_json cũng theo giá trị này: `_snapshot_for` dựng
    # snapshot từ gốc được truyền vào, nên không còn nhánh nào tự đoán "output".
    output_dir: str = "output"


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
    #   v9: "max_chars" stated as a hard limit with a self-check, the review pass
    #       told not to lengthen a draft, and the advertised budget cut to 90% of the
    #       real ceiling. Sending the ceiling itself left no slack — S5 warns at the
    #       same CPS the budget comes from — and the over-budget share had climbed
    #       from 6% to 15% as translation quality work went in.
    #   v8: the glossary block now pins one third-person form for the main subject.
    #       Rule 3 only ever offered a menu, so each batch chose again: measured on a
    #       472-line video, the dominant form covered 77-90% of occurrences and one
    #       run settled on "anh", which rule 3 does not even list.
    prompt_version: int = 9


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
    provider: Literal["openai", "anthropic", "chatgpt_web"] = "openai"
    base_url: str = "https://api.openai.com/v1"
    api_key_env: str = "OPENAI_API_KEY"
    model: str = _PLACEHOLDER
    temperature: float = 0.0
    max_retries: int = 3
    timeout_sec: float = 120.0
    _credential_store: CredentialStore = PrivateAttr(
        default_factory=get_default_credential_store
    )

    def bind_credential_store(self, store: CredentialStore) -> LLMProfile:
        self._credential_store = store
        return self

    def credential_available(self) -> bool:
        if self.provider == "chatgpt_web" or not self.api_key_env:
            return True
        try:
            return resolve_secret(self.api_key_env, self._credential_store) is not None
        except CredentialStoreError:
            return False

    def api_key(self) -> str:
        """Resolve the key from the environment, then the OS credential vault.

        An empty ``api_key_env`` means the endpoint needs no auth — Ollama, LM
        Studio and vLLM all ignore the header. They do still reject a *missing*
        Authorization header on some builds, so a placeholder goes out instead.
        """
        if not self.api_key_env:
            return "local"
        key = resolve_secret(self.api_key_env, self._credential_store)
        if not key:
            raise RuntimeError(
                f"Chưa có API key cho {self.api_key_env}. "
                f'Đặt nó rồi chạy lại, hoặc để api_key_env = "" nếu endpoint chạy local.'
            )
        return key

    def require_model(self, who: str) -> str:
        if not self.model or self.model == _PLACEHOLDER:
            raise RuntimeError(
                f"Chưa đặt model cho [llm.{who}] trong zhsub.toml (đang là {_PLACEHOLDER!r})."
            )
        return self.model


class ChatGPTWebConfig(BaseModel):
    """Browser settings for ``provider = "chatgpt_web"``.

    One block for the whole file rather than one per profile: Chromium refuses to
    open the same user-data directory twice, so segment and translate share a
    browser whether or not the config pretends otherwise.
    """

    profile_dir: str = "data/chrome_profile"
    # Optional. Absent — the recommended setup — login happens by hand once and the
    # profile remembers it, which keeps the ChatGPT password off disk entirely.
    account_file: str = "data/chatgpt_account.json"
    manual_login_timeout_sec: float = 300.0


class LLMConfig(BaseModel):
    segment: LLMProfile = Field(default_factory=LLMProfile)
    translate: LLMProfile = Field(
        default_factory=lambda: LLMProfile(temperature=0.3, timeout_sec=180.0)
    )
    chatgpt_web: ChatGPTWebConfig = Field(default_factory=ChatGPTWebConfig)


class DubConfig(BaseModel):
    """Sinh audio lồng tiếng từ bản dịch (S6, không nằm trong `zhsub run`).

    Tách khỏi pipeline mặc định vì mỗi lần chạy tốn tiền thật: một cue là một lần
    gọi API, video 17 phút hết 184 lần.
    """

    base_url: str = "https://api.ai33.pro"
    api_key_env: str = "AI33_API_KEY"
    voice_id: str = _PLACEHOLDER

    # Tốc độ đọc cho MỌI cue. Nghe thử thì 1.0 vừa tai — cảm giác "gấp" của bản đầu
    # không đến từ tốc độ đọc mà từ chỗ giao giữa các câu bị dán sát (xem min_gap_sec).
    base_speed: float = 1.0

    # Trần khi câu dài hơn chỗ trống. Bằng base_speed nghĩa là KHÔNG BAO GIỜ tăng
    # tốc — nghe thử bản có tăng tốc (tới 1.15x) thì rõ là gấp gáp. Câu dài giờ lấn
    # sang khoảng lặng phía sau, và hàm ghép đẩy lùi rồi canh lại ở cue kế tiếp nên
    # sai số không tích luỹ. Nới lên nếu chấp nhận đánh đổi ngược lại.
    max_speed: float = 1.0

    # Nhịp thở tối thiểu giữa hai câu. Cắt lặng bỏ mất đuôi im lặng của nhà cung
    # cấp, nên câu bị đẩy lùi sẽ dán khít vào đuôi câu trước và nghe rất gấp ở chỗ
    # giao. 0.25s xấp xỉ nhịp của chính video gốc: 50 giây khoảng lặng chia cho 184
    # cue là 0.27s. Tổng thời lượng đọc dư 25% nên thừa chỗ cho khoản này.
    min_gap_sec: float = 0.25

    # Hiệu chuẩn của giọng đang dùng: thời lượng = overhead + số_âm_tiết x hệ_số.
    # Đo bằng cách TTS 8 câu dài ngắn khác nhau rồi khớp tuyến tính, SAU khi đã cắt
    # lặng hai đầu. Số mặc định lấy từ vbee_n_hn_male_duyonyx_oaistable_vc ở speed 1;
    # đổi giọng thì phải đo lại, nếu không phần chỉnh tốc độ sẽ sai.
    overhead_sec: float = 0.15
    sec_per_syllable: float = 0.217

    sample_rate: int = 24000

    # Số cue tổng hợp song song. Chạy tuần tự mất ~58 giây mỗi cue, tức hơn 2 tiếng
    # cho video 184 cue — và gần như toàn bộ là chờ server tổng hợp chứ không phải
    # xử lý gì ở đây, nên tăng luồng gần như tỉ lệ thuận (4 luồng: 7 phút xuống 2:54
    # trên clip 9 cue).
    #
    # 4, không phải 8. Thử 8 thì job 184 cue chết ở cue thứ 82 vì `server_busy` liên
    # tục. Lý do chọn 8 lúc đầu là sai: header rate-limit (10 request/giây, burst
    # 20) nói về TỐC ĐỘ REQUEST, còn thứ bị quá tải là NĂNG LỰC TỔNG HỢP của
    # backend — hai tài nguyên khác nhau, và `server_busy` là tín hiệu của cái thứ
    # hai. 4 luồng đã chạy sạch trên clip thử.
    concurrency: int = 4
    _credential_store: CredentialStore = PrivateAttr(
        default_factory=get_default_credential_store
    )

    def bind_credential_store(self, store: CredentialStore) -> DubConfig:
        self._credential_store = store
        return self

    def credential_available(self) -> bool:
        try:
            return resolve_secret(self.api_key_env, self._credential_store) is not None
        except CredentialStoreError:
            return False

    def api_key(self) -> str:
        key = resolve_secret(self.api_key_env, self._credential_store)
        if not key:
            raise RuntimeError(f"Chưa có API key cho {self.api_key_env}.")
        return key

    def configured_voice_id(self) -> str | None:
        """Giọng ghi trong zhsub.toml, hoặc None khi còn là placeholder.

        Trả None thay vì ném lỗi vì mọi nơi gọi đều muốn "không có thì lấy giọng
        đã lưu của job" — một hàm require_voice() ném lỗi từng tồn tại ở đây và
        không nơi nào dùng được, nên năm chỗ tự viết lại phép kiểm placeholder.
        """
        voice = self.voice_id.strip()
        return voice if voice and voice != _PLACEHOLDER else None


class Config(BaseModel):
    paths: PathsConfig = Field(default_factory=PathsConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    asr: AsrConfig = Field(default_factory=AsrConfig)
    segment: SegmentConfig = Field(default_factory=SegmentConfig)
    translate: TranslateConfig = Field(default_factory=TranslateConfig)
    render: RenderConfig = Field(default_factory=RenderConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    dub: DubConfig = Field(default_factory=DubConfig)

    def bind_credential_store(self, store: CredentialStore) -> Config:
        self.llm.segment.bind_credential_store(store)
        self.llm.translate.bind_credential_store(store)
        self.dub.bind_credential_store(store)
        return self

    @classmethod
    def load(
        cls,
        path: str | Path | None = None,
        *,
        credential_store: CredentialStore | None = None,
    ) -> Config:
        """Load config, falling back to ``zhsub.toml`` then the example file.

        Finding neither is fine — defaults are usable, which matters for the
        benchmark stage since it never touches an LLM.
        """
        if path is not None:
            config = cls._from_file(Path(path))
        else:
            config = cls()
            for name in DEFAULT_CONFIG_NAMES:
                candidate = Path.cwd() / name
                if candidate.is_file():
                    config = cls._from_file(candidate)
                    break
        return (
            config.bind_credential_store(credential_store)
            if credential_store is not None
            else config
        )

    @classmethod
    def _from_file(cls, path: Path) -> Config:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        return cls.model_validate(raw)
