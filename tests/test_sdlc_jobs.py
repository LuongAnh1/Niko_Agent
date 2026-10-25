import threading
import time
import unittest
from unittest.mock import patch

from niko.sdlc.client import ROUTE_SDLC_CONTINUE, SdlcResult
from niko.sdlc.jobs import SdlcJob, SdlcJobRunner


class FakeTraceLogger:
    def __init__(self) -> None:
        self.events = []
        self.lock = threading.Lock()

    def event(self, trace_id, kind, data):
        with self.lock:
            self.events.append((trace_id, kind, data))


def make_job(run_id: str = "run_20261012_120000_abcd1234") -> SdlcJob:
    return SdlcJob(
        run_id=run_id,
        action=ROUTE_SDLC_CONTINUE,
        conversation_id="chat-1",
        user_key="telegram:1",
        trace_id=f"trace-{run_id}",
        started_at=time.time(),
    )


def wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


class SdlcJobRunnerTests(unittest.TestCase):
    def test_queued_callback_precedes_task_and_final_runs_once(self):
        trace = FakeTraceLogger()
        runner = SdlcJobRunner(trace_logger=trace)
        order = []
        completed = threading.Event()
        result = SdlcResult(ok=True, action=ROUTE_SDLC_CONTINUE, run_id=make_job().run_id, status="code_review_ready")

        def task():
            order.append("task")
            return result

        def on_complete(received):
            order.append(("complete", received))
            completed.set()

        with patch.dict("os.environ", {"NIKO_RUNTIME_LOG_ENABLED": "0"}, clear=False):
            started = runner.submit(make_job(), task, lambda: order.append("queued"), on_complete)
            self.assertTrue(completed.wait(2))

        self.assertTrue(started)
        self.assertEqual(order, ["queued", "task", ("complete", result)])
        self.assertTrue(wait_until(lambda: runner.get_active(result.run_id) is None))
        kinds = [item[1] for item in trace.events]
        self.assertEqual(
            kinds,
            ["sdlc_job_queued", "sdlc_job_started", "sdlc_job_finished", "sdlc_job_result_delivered"],
        )

    def test_duplicate_run_is_rejected_but_different_runs_execute(self):
        runner = SdlcJobRunner(trace_logger=FakeTraceLogger())
        release = threading.Event()
        started_runs = []
        completed = threading.Event()
        completion_count = 0
        completion_lock = threading.Lock()

        def task_for(run_id):
            def task():
                started_runs.append(run_id)
                release.wait(2)
                return SdlcResult(ok=True, action=ROUTE_SDLC_CONTINUE, run_id=run_id)

            return task

        def on_complete(_result):
            nonlocal completion_count
            with completion_lock:
                completion_count += 1
                if completion_count == 2:
                    completed.set()

        first = make_job("run_20261012_120000_abcd1234")
        second = make_job("run_20261012_120001_abcd5678")
        with patch.dict("os.environ", {"NIKO_RUNTIME_LOG_ENABLED": "0"}, clear=False):
            self.assertTrue(runner.submit(first, task_for(first.run_id), lambda: None, on_complete))
            self.assertTrue(wait_until(lambda: first.run_id in started_runs))
            self.assertFalse(runner.submit(make_job(first.run_id), task_for(first.run_id), lambda: None, on_complete))
            self.assertTrue(runner.submit(second, task_for(second.run_id), lambda: None, on_complete))
            self.assertTrue(wait_until(lambda: second.run_id in started_runs))
            release.set()
            self.assertTrue(completed.wait(2))

        self.assertEqual(sorted(started_runs), sorted([first.run_id, second.run_id]))
        self.assertTrue(wait_until(lambda: runner.get_active(first.run_id) is None))
        self.assertTrue(wait_until(lambda: runner.get_active(second.run_id) is None))

    def test_error_results_and_exceptions_cleanup_active_job(self):
        cases = [
            (
                "run_20261012_120000_abcd1234",
                lambda: SdlcResult(
                    ok=False,
                    action=ROUTE_SDLC_CONTINUE,
                    run_id="run_20261012_120000_abcd1234",
                    error_code="sdlc_timeout",
                ),
                "sdlc_timeout",
            ),
            (
                "run_20261012_120001_abcd5678",
                lambda: (_ for _ in ()).throw(RuntimeError("secret raw failure")),
                "sdlc_background_error",
            ),
        ]

        for run_id, task, expected_code in cases:
            with self.subTest(error_code=expected_code):
                runner = SdlcJobRunner(trace_logger=FakeTraceLogger())
                completed = threading.Event()
                results = []

                def on_complete(result):
                    results.append(result)
                    completed.set()

                with patch.dict("os.environ", {"NIKO_RUNTIME_LOG_ENABLED": "0"}, clear=False):
                    self.assertTrue(runner.submit(make_job(run_id), task, lambda: None, on_complete))
                    self.assertTrue(completed.wait(2))

                self.assertEqual(len(results), 1)
                self.assertEqual(results[0].error_code, expected_code)
                self.assertNotIn("secret raw failure", results[0].message)
                self.assertTrue(wait_until(lambda: runner.get_active(run_id) is None))


if __name__ == "__main__":
    unittest.main()
