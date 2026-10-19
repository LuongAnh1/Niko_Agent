"""Vòng đời background job khi Niko chờ SDLC HTTP operation dài."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import threading
import time

from niko.harness.runtime_log import default_runtime_logger
from niko.sdlc.client import SdlcResult


@dataclass(frozen=True)
class SdlcJob:
    """Safe metadata needed to observe one background SDLC action."""

    run_id: str
    action: str
    conversation_id: str
    user_key: str
    trace_id: str
    started_at: float


class SdlcJobRunner:
    """Run at most one background SDLC action for each run id."""

    def __init__(self, trace_logger=None) -> None:
        self._active_jobs: dict[str, SdlcJob] = {}
        self._lock = threading.Lock()
        self._trace_logger = trace_logger

    def get_active(self, run_id: str) -> SdlcJob | None:
        with self._lock:
            return self._active_jobs.get(run_id)

    def submit(
        self,
        job: SdlcJob,
        task: Callable[[], SdlcResult],
        on_reserved: Callable[[], None],
        on_complete: Callable[[SdlcResult], None],
    ) -> bool:
        """Reserve before replying, then start a daemon worker after the queued reply."""

        with self._lock:
            duplicate = job.run_id in self._active_jobs
            if not duplicate:
                self._active_jobs[job.run_id] = job

        if duplicate:
            self._emit("sdlc_job_duplicate", job)
            return False

        self._emit("sdlc_job_queued", job)
        try:
            on_reserved()
        except Exception:
            with self._lock:
                self._active_jobs.pop(job.run_id, None)
            raise

        thread = threading.Thread(
            target=self._run_job,
            args=(job, task, on_complete),
            daemon=True,
            name=f"niko-sdlc-{job.run_id}",
        )
        thread.start()
        return True

    def _run_job(
        self,
        job: SdlcJob,
        task: Callable[[], SdlcResult],
        on_complete: Callable[[SdlcResult], None],
    ) -> None:
        self._emit("sdlc_job_started", job)
        result: SdlcResult
        try:
            result = task()
        except Exception:
            result = SdlcResult(
                ok=False,
                action=job.action,
                run_id=job.run_id,
                error_code="sdlc_background_error",
                message="Tác vụ SDLC chạy nền gặp lỗi ngoài hợp đồng.",
            )

        duration_seconds = max(0.0, time.time() - job.started_at)
        event = "sdlc_job_finished" if result.ok else "sdlc_job_failed"
        self._emit(
            event,
            job,
            status=result.status,
            error_code=result.error_code,
            duration_seconds=round(duration_seconds, 3),
        )
        try:
            on_complete(result)
            self._emit(
                "sdlc_job_result_delivered",
                job,
                status=result.status,
                error_code=result.error_code,
                duration_seconds=round(duration_seconds, 3),
            )
        except Exception:
            self._emit(
                "sdlc_job_failed",
                job,
                status=result.status,
                error_code="sdlc_result_callback_error",
                duration_seconds=round(duration_seconds, 3),
            )
        finally:
            with self._lock:
                self._active_jobs.pop(job.run_id, None)

    def _emit(
        self,
        event: str,
        job: SdlcJob,
        *,
        status: str = "",
        error_code: str = "",
        duration_seconds: float | None = None,
    ) -> None:
        elapsed = duration_seconds if duration_seconds is not None else max(0.0, time.time() - job.started_at)
        data = {
            "action": job.action,
            "run_id": job.run_id,
            "status": status,
            "error_code": error_code,
            "duration_seconds": round(elapsed, 3),
            "conversation_id": job.conversation_id,
            "trace_id": job.trace_id,
        }

        try:
            if self._trace_logger is not None:
                self._trace_logger.event(job.trace_id, event, data)
        except Exception:
            pass

        level = "error" if event == "sdlc_job_failed" else "info"
        default_runtime_logger().event(
            "sdlc",
            event,
            f"SDLC background job: {event}",
            level=level,
            data=data,
        )


__all__ = ["SdlcJob", "SdlcJobRunner"]
