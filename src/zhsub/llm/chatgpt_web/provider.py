"""ChatGPT's web UI driven by Playwright, exposed as an :class:`LLMProvider`.

No API key and no per-token billing — the browser reuses a logged-in ChatGPT
session. What it costs instead is fragility: the selectors belong to a UI OpenAI
changes without notice, and driving that UI automatically is against OpenAI's
terms of service. Treat it as the fallback for when no API budget exists, not as
the default path.
"""

from __future__ import annotations

import logging

from ...config import ChatGPTWebConfig
from ..base import LLMError, LLMProvider, NonRetryableLLMError
from .chat import ChatGPTContentRefusal, ChatGPTResponseError, ask
from .session import BrowserSessionUnavailable, get_session

log = logging.getLogger(__name__)

# The composer is a single text box: no system role, no response_format. Both have
# to be folded into the one prompt.
_SYSTEM_SEPARATOR = "\n\n---\n\n"
_JSON_INSTRUCTION = "\n\nReply with raw JSON only: no markdown fences, no commentary."


def compose_prompt(system: str, user: str, json_mode: bool) -> str:
    prompt = f"{system}{_SYSTEM_SEPARATOR}{user}" if system else user
    return prompt + _JSON_INSTRUCTION if json_mode else prompt


class ChatGPTWebProvider(LLMProvider):
    def __init__(
        self,
        model: str,
        web: ChatGPTWebConfig,
        temperature: float = 0.0,
        max_retries: int = 3,
        timeout_sec: float = 300.0,
    ) -> None:
        # temperature has no equivalent in the web UI; it stays on the base class
        # only so every provider reports the same fields.
        super().__init__(model, temperature, max_retries, timeout_sec)
        self._web = web
        # One conversation per system prompt, not one per provider. S4 with
        # review_pass alternates between the plain rules and the rules plus review
        # suffix on every single call; remembering only the latest meant each turn
        # saw a different system prompt, opened a new chat, and the shared thread
        # degenerated into a fresh chat per call — 16 conversations for 16 calls.
        self._conversations: dict[str, str] = {}

    def _call(self, system: str, user: str, cache_system: bool, json_mode: bool = False) -> str:
        # cache_system is meaningless here: there is no prompt cache behind the web UI.
        #
        # The system prompt doubles as the conversation key. It is constant for the
        # whole of S2, for S3's term blocks, and for S4 within one target language,
        # but differs between them — so keying on it puts each stage in its own
        # thread without any stage having to know this provider exists. The threads
        # are per instance, and `build_provider` runs once per stage per job, so two
        # jobs in a `batch` run never share one.
        conversation = self._conversations.get(system)

        # The rules are restated on every turn even though the thread already holds
        # them. Measured on the 472-line clip: stating them once and relying on the
        # thread let the model drift — "ông ấy" appeared 10 times against 0 for the
        # one-shot-chat run, because by batch 5 rule 3's list of accepted pronouns
        # had scrolled far up the conversation. Re-sending costs no tokens here.
        prompt = compose_prompt(system, user, json_mode)

        try:
            session = get_session(self._web)
            with session.page() as page:
                text, url = session.run(ask(page, prompt, int(self.timeout_sec), conversation))
        except BrowserSessionUnavailable as exc:
            raise LLMError(
                "ChatGPT Playwright đã dừng; hệ thống sẽ tự khởi động lại và thử tiếp."
            ) from exc
        except ChatGPTContentRefusal as exc:
            # Vẫn là LLMError nên stage chạy được đường phục hồi của mình, nhưng
            # vòng thử lại bỏ qua — gửi lại đúng văn bản đó chỉ nhận đúng lời từ
            # chối đó. Đo trên một video án mạng: mỗi lần bị chặn tốn 12 lần gọi
            # trước khi tới được bước chia đôi batch, cả 12 đều gửi một nội dung.
            #
            # Bỏ luôn hội thoại: một thread đã từ chối một lần có thể kéo theo
            # các lượt sau, nên lượt kế tiếp mở chat mới thay vì nối vào đó. Đây
            # là suy đoán chứ chưa đo được, nhưng mở chat mới không tốn gì.
            self._conversations.pop(system, None)
            log.warning("ChatGPT chặn nội dung — bỏ thread này, để stage tự xử")
            raise NonRetryableLLMError(f"ChatGPT web: {exc}") from exc
        except ChatGPTResponseError as exc:
            # Retryable: a slow or truncated answer usually comes back fine on the
            # next attempt. Login and rate-limit errors deliberately propagate as
            # they are, so the job fails fast instead of retrying blind.
            raise LLMError(f"ChatGPT web: {exc}") from exc

        if url:
            if conversation is None:
                # Logged so a run can be audited afterwards: open the URL to see exactly
                # what the model was told and answered, which no log line can reproduce.
                log.info("ChatGPT: hội thoại mới %s", url)
            self._conversations[system] = url
        return text
