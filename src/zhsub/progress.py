"""Progress reporting and cancellation for embedding hosts.

A desktop app needs three things the CLI does not: a progress bar, a Cancel
button, and errors it can present rather than a traceback. All three come down to
one object threaded through the stages.

Cancellation is checked at stage boundaries and inside the long loops, never
mid-write. That is safe because every stage writes its JSON atomically at the end:
a cancelled run loses only the stage in flight, and ``resume`` continues from the
last completed one. In S4 the translation cache is flushed per batch, so even
inside the cancelled stage nothing already paid for is lost.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Protocol

STAGE_LABELS = {
    "ingest": "Chuẩn hoá audio",
    "asr": "Nhận dạng giọng nói",
    "segment": "Ngắt câu",
    "glossary": "Trích thuật ngữ",
    "translate": "Dịch",
    "render": "Xuất phụ đề",
}

# Rough share of a full run per stage, for a single overall progress bar. Measured
# on a 35-minute video with ASR on GPU and both LLM stages on an API: ASR is fast
# (RTF 0.01) while translation dominates.
STAGE_WEIGHTS = {
    "ingest": 0.02,
    "asr": 0.08,
    "segment": 0.15,
    "glossary": 0.10,
    "translate": 0.60,
    "render": 0.05,
}


class Cancelled(RuntimeError):
    """Raised when the host asked the job to stop. Work already written is kept."""


@dataclass(frozen=True, slots=True)
class Progress:
    """One progress update.

    ``fraction`` is overall completion across the whole job in ``0.0..1.0``, so a
    host can bind it straight to a progress bar without knowing the stage list.
    """

    job_id: str
    stage: str
    stage_label: str
    stage_fraction: float  # 0..1 within the current stage
    fraction: float  # 0..1 across the whole job
    message: str = ""


ProgressCallback = Callable[[Progress], None]


class Canceller(Protocol):
    def is_set(self) -> bool: ...


class RunContext:
    """Carries the host's callback and cancel flag down through the stages.

    Passing ``None`` anywhere a context is accepted disables both, which is what
    the CLI does — no branching in the stages themselves.
    """

    def __init__(
        self,
        job_id: str = "",
        on_progress: ProgressCallback | None = None,
        cancel: Canceller | None = None,
        stages: list[str] | None = None,
    ) -> None:
        self.job_id = job_id
        self._on_progress = on_progress
        self._cancel = cancel or threading.Event()
        self._stages = stages or list(STAGE_WEIGHTS)
        self._stage: str = self._stages[0] if self._stages else "ingest"

    # -- cancellation ------------------------------------------------------

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled():
            raise Cancelled(f"Job {self.job_id} bị huỷ ở stage {self._stage}")

    # -- progress ----------------------------------------------------------

    def enter_stage(self, stage: str) -> None:
        self._stage = stage
        self.report(0.0)

    def report(self, stage_fraction: float, message: str = "") -> None:
        """Report progress within the current stage, and check for cancellation.

        Reporting and cancellation are deliberately the same call: every place worth
        reporting from is a safe place to stop, so a host that wires up a progress
        bar gets a responsive Cancel button for free.
        """
        self.raise_if_cancelled()
        if self._on_progress is None:
            return

        stage_fraction = min(max(stage_fraction, 0.0), 1.0)
        done = sum(STAGE_WEIGHTS.get(s, 0.0) for s in self._stages_before(self._stage))
        current = STAGE_WEIGHTS.get(self._stage, 0.0)
        total = sum(STAGE_WEIGHTS.get(s, 0.0) for s in self._stages) or 1.0

        self._on_progress(
            Progress(
                job_id=self.job_id,
                stage=self._stage,
                stage_label=STAGE_LABELS.get(self._stage, self._stage),
                stage_fraction=stage_fraction,
                fraction=(done + current * stage_fraction) / total,
                message=message,
            )
        )

    def _stages_before(self, stage: str) -> list[str]:
        if stage not in self._stages:
            return []
        return self._stages[: self._stages.index(stage)]


def ensure_context(ctx: RunContext | None) -> RunContext:
    """Return ``ctx``, or a fresh inert one.

    Deliberately not a module-level singleton: a shared instance would share one
    cancel flag, so cancelling any job would silently cancel every other job in the
    process — exactly what `batch --concurrency 2` would hit.
    """
    return ctx if ctx is not None else RunContext()
