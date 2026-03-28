from __future__ import annotations

import json
import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from models.task import Task
from utils.manual_supervisor import ManualSupervisorSession
from utils.token_tracker import TokenTracker, save_session_to_log


class BridgeResilienceTests(unittest.TestCase):
    def test_safe_stdout_write_swallows_os_error(self) -> None:
        fake_stdout = mock.Mock()
        fake_stdout.write.side_effect = OSError(22, "Invalid argument")

        with mock.patch.object(sys, "stdout", fake_stdout):
            succeeded = main._safe_stdout_write("hello")

        self.assertFalse(succeeded)

    def test_find_unexpected_files_ignores_python_runtime_artifacts(self) -> None:
        before_snapshot = {"app.py"}
        after_snapshot = {
            "app.py",
            "module/__pycache__/app.cpython-313.pyc",
            "module/output.txt",
        }
        task = Task(
            id=1,
            files=["app.py"],
            instruction="Modify app.py",
            type="modify",
        )

        unexpected_files = main._find_unexpected_files(before_snapshot, after_snapshot, task)

        self.assertEqual(["module/output.txt"], unexpected_files)

    def test_manual_supervisor_resumes_completed_task_when_files_match(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_root = Path(temp_dir)
            target_file = repo_root / "module.py"
            target_file.write_text("print('ok')\n", encoding="utf-8")

            session = ManualSupervisorSession(repo_root, logging.getLogger("test"))
            session.record_completed_review(
                task_id=7,
                instruction="Modify module.py",
                files=["module.py"],
                file_paths=[target_file],
                diff="diff-data",
            )

            resumed_diff = session.try_resume_completed_task(
                task_id=7,
                instruction="Modify module.py",
                files=["module.py"],
                file_paths=[target_file],
            )

            self.assertEqual("diff-data", resumed_diff)

    def test_token_log_separates_zero_progress_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            log_path = Path(temp_dir) / "token_log.json"

            productive_tracker = TokenTracker()
            productive_tracker.record_session_tokens(1200, is_estimate=False)
            productive_session = productive_tracker.build_session_report(
                goal="Test",
                repo_root=Path(temp_dir),
                supervisor_command="manual",
                tasks_executed=2,
                tasks_skipped=0,
                elapsed_seconds=10.0,
            )
            save_session_to_log(productive_session, log_path)

            zero_progress_tracker = TokenTracker()
            zero_progress_tracker.record_session_tokens(800, is_estimate=False)
            zero_progress_session = zero_progress_tracker.build_session_report(
                goal="Test",
                repo_root=Path(temp_dir),
                supervisor_command="manual",
                tasks_executed=0,
                tasks_skipped=0,
                elapsed_seconds=5.0,
                failure_reason="OSError: [Errno 22] Invalid argument",
            )
            save_session_to_log(zero_progress_session, log_path)

            payload = json.loads(log_path.read_text(encoding="utf-8"))
            totals = payload["totals"]

            self.assertEqual(1, totals["wasted_sessions_count"])
            self.assertEqual("bridge_stdout_crash", payload["sessions"][0]["productivity"]["waste_reason"])
            self.assertGreater(totals["wasted_tokens_total"], 0)
            self.assertGreater(totals["savings_percent_successful_avg"], 0.0)
            self.assertLess(
                totals["savings_percent_weighted"],
                totals["savings_percent_successful_avg"],
            )


if __name__ == "__main__":
    unittest.main()
