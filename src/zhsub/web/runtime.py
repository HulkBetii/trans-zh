"""Lane-aware durable scheduler and fan-out notifications for the web API."""

from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Config
from ..jobs import TTS_PROVIDER, Run, JobStore
from ..progress import Cancelled, Progress

Runner = Callable[..., Any]
TtsRunner = Callable[[Any, Run, Config, Callable[[Progress], None], threading.Event], Any]


class RunEventBroker:
    """Wake every SSE subscriber; durable events remain in SQLite."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._generation: dict[str, int] = {}

    def generation(self, run_id: str) -> int:
        with self._condition:
            return self._generation.get(run_id, 0)

    def notify(self, run_id: str) -> None:
        with self._condition:
            self._generation[run_id] = self._generation.get(run_id, 0) + 1
            self._condition.notify_all()

    def wait(self, run_id: str, generation: int, timeout: float) -> int:
        with self._condition:
            self._condition.wait_for(
                lambda: self._generation.get(run_id, 0) != generation,
                timeout=timeout,
            )
            return self._generation.get(run_id, 0)


class RunScheduler:
    """Execute one run at a time in each lane.

    The pipeline lane remains serialized so only one ASR/LLM workload uses the
    local resources.  TTS has its own worker and can therefore make progress for a
    different job without blocking the pipeline lane.  ``JobStore`` still prevents
    two active runs for the same job, regardless of lane.
    """

    def __init__(
        self,
        config: Config,
        store: JobStore,
        *,
        runner: Runner | None = None,
        tts_runner: TtsRunner | None = None,
        broker: RunEventBroker | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.runner = runner or self._default_runner
        self.tts_runner = tts_runner or self._default_tts_runner
        self.broker = broker or RunEventBroker()
        self.control_lock = threading.RLock()
        self._condition = threading.Condition()
        self._stop = threading.Event()
        self._threads: dict[str, threading.Thread] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._cancel_lock = threading.Lock()

    def start(self) -> None:
        with self._condition:
            if any(thread.is_alive() for thread in self._threads.values()):
                return
            self.store.recover_runs()
            self._stop.clear()
            self._threads = {
                lane: threading.Thread(
                    target=self._worker,
                    args=(lane,),
                    daemon=True,
                    name=f"zhsub-web-{lane}-runner",
                )
                for lane in ("pipeline", "tts")
            }
            for thread in self._threads.values():
                thread.start()
            self._condition.notify_all()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        for thread in tuple(self._threads.values()):
            thread.join(timeout=timeout)

    def enqueue(
        self,
        job_id: str,
        kind: str,
        from_stage: str,
        *,
        lane: str = "pipeline",
        force: bool = False,
        payload: dict[str, Any] | None = None,
    ) -> Run:
        with self.control_lock:
            run = self.store.create_run(
                job_id,
                kind,
                from_stage,
                lane=lane,
                force=force,
                payload=payload,
            )
        self._wake(run.run_id)
        return run

    def create_job_run(
        self,
        job_id: str,
        source_uri: str,
        work_dir: str,
        targets: list[str],
        request: dict[str, Any],
    ) -> Run:
        with self.control_lock:
            _, run = self.store.upsert_and_create_run(
                job_id,
                source_uri,
                work_dir,
                targets,
                request,
            )
        self._wake(run.run_id)
        return run

    def _wake(self, run_id: str) -> None:
        self.broker.notify(run_id)
        with self._condition:
            self._condition.notify_all()

    def cancel(self, run_id: str) -> Run | None:
        run = self.store.request_cancel(run_id)
        if run is None:
            return None
        if run.status == "cancelling":
            with self._cancel_lock:
                cancel_event = self._cancel_events.get(run_id)
            if cancel_event is not None:
                cancel_event.set()
        self.broker.notify(run_id)
        with self._condition:
            self._condition.notify_all()
        return run

    def _worker(self, lane: str) -> None:
        while not self._stop.is_set():
            run = self.store.claim_next_run(lane)
            if run is None:
                with self._condition:
                    self._condition.wait(timeout=1.0)
                continue
            self.broker.notify(run.run_id)
            self._execute(run)

    def _execute(self, run: Run) -> None:
        job = self.store.get(run.job_id)
        if job is None or (run.lane == "pipeline" and job.request is None):
            self.store.finish_run(
                run.run_id,
                "failed",
                error="Job request snapshot is missing",
                stage=run.from_stage,
            )
            self.broker.notify(run.run_id)
            return

        cancel_event = threading.Event()
        with self._cancel_lock:
            self._cancel_events[run.run_id] = cancel_event
        # Cancellation may win the small race between claiming the run and
        # registering its in-memory event.
        current = self.store.get_run(run.run_id)
        if current is not None and current.status == "cancelling":
            cancel_event.set()

        def on_progress(progress: Progress) -> None:
            self.store.update_run_progress(
                run.run_id,
                stage=progress.stage,
                progress=progress.fraction,
                message=progress.message,
                stage_label=progress.stage_label,
                stage_progress=progress.stage_fraction,
            )
            self.broker.notify(run.run_id)

        try:
            if run.lane == "pipeline":
                assert job.request is not None
                snapshot = job.request
                source = snapshot.get("source") or {}
                self.runner(
                    source.get("value", job.source_uri),
                    targets=list(snapshot.get("targets") or job.targets or ["vi"]),
                    out_dir=Path(snapshot.get("output_dir") or "output"),
                    config=self.config,
                    bilingual=bool(snapshot.get("bilingual", False)),
                    formats=tuple(snapshot.get("formats") or ("srt", "ass")),
                    from_stage=run.from_stage,
                    force=run.force,
                    on_progress=on_progress,
                    cancel=cancel_event,
                )
            else:
                self.tts_runner(job, run, self.config, on_progress, cancel_event)
        except Cancelled:
            self.store.finish_run(run.run_id, "cancelled")
        except Exception as exc:  # noqa: BLE001 - persisted for UI presentation
            current = self.store.get_run(run.run_id)
            if cancel_event.is_set() or (current is not None and current.status == "cancelling"):
                self.store.finish_run(run.run_id, "cancelled")
            else:
                self.store.finish_run(
                    run.run_id,
                    "failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
        else:
            current = self.store.get_run(run.run_id)
            final_status = "cancelled" if current and current.status == "cancelling" else "completed"
            self.store.finish_run(run.run_id, final_status)
        finally:
            with self._cancel_lock:
                self._cancel_events.pop(run.run_id, None)
            self.broker.notify(run.run_id)

    @staticmethod
    def _default_runner(*args: Any, **kwargs: Any) -> Any:
        from ..api import translate_video

        return translate_video(*args, **kwargs)

    def _default_tts_runner(
        self,
        job: Any,
        run: Run,
        config: Config,
        on_progress: Callable[[Progress], None],
        cancel: threading.Event,
    ) -> Any:
        """Run the built-in S6 adapter when the host does not inject one."""
        from ..dub.calibrate import calibrate_voice
        from ..progress import RunContext
        from ..stages import s6_dub

        payload = run.payload or {}
        work_dir = Path(job.work_dir)
        lang = str(payload.get("lang") or "vi")
        voice_id = payload.get("voice_id")
        calibration = payload.get("calibration")
        if calibration is None and voice_id:
            stored = self.store.get_voice_calibration(TTS_PROVIDER, str(voice_id))
            if stored is not None:
                calibration = {
                    "voice_id": stored.voice_id,
                    "overhead_sec": stored.overhead_sec,
                    "sec_per_syllable": stored.sec_per_syllable,
                    "sample_count": stored.sample_count,
                    "samples_hash": payload.get("calibration_hash", "stored"),
                    "created_at": str(stored.updated_at),
                    "points": [],
                }

        if run.kind == "tts_preview":
            segment_id = int(payload["segment_id"])
            on_progress(Progress(job.job_id, run.from_stage, "TTS preview", 0.0, 0.0))
            result = s6_dub.preview_cue(
                work_dir,
                config,
                segment_id,
                lang=lang,
                voice_id=str(voice_id) if voice_id else None,
                calibration=calibration,
                force=run.force,
            )
            on_progress(Progress(job.job_id, run.from_stage, "TTS preview", 1.0, 1.0))
            return result

        if run.kind == "tts_calibrate":
            sample_texts = payload.get("sample_texts")
            on_progress(Progress(job.job_id, run.from_stage, "TTS calibration", 0.0, 0.0))

            def report(fraction: float, message: str) -> None:
                on_progress(
                    Progress(job.job_id, run.from_stage, "TTS calibration", fraction, fraction, message)
                )
                if cancel.is_set():
                    from ..progress import Cancelled

                    raise Cancelled("TTS calibration cancelled")

            calibration_result = calibrate_voice(
                work_dir,
                config,
                lang,
                samples=8,
                voice_id=str(voice_id) if voice_id else None,
                sample_texts=sample_texts if isinstance(sample_texts, list) else None,
                progress=report,
            )
            self.store.save_voice_calibration(
                TTS_PROVIDER,
                calibration_result.voice_id,
                calibration_result.overhead_sec,
                calibration_result.sec_per_syllable,
                sample_count=calibration_result.sample_count,
                source_job_id=job.job_id,
            )
            return calibration_result

        if run.kind == "tts_render":
            output_dir = Path(payload.get("output_dir") or "output")
            ctx = RunContext(
                job_id=job.job_id,
                on_progress=on_progress,
                cancel=cancel,
                stages=[run.from_stage],
            )
            return s6_dub.run(
                work_dir,
                config,
                lang,
                output_dir,
                force=run.force,
                ctx=ctx,
                voice_id=str(voice_id) if voice_id else None,
                calibration=calibration,
                subtitle_approval_signature=str(
                    payload.get("subtitle_approval_signature") or ""
                ),
            )

        raise RuntimeError(f"Unsupported TTS run kind: {run.kind}")
