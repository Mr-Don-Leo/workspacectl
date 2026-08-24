import sys
import time
import unittest

from devws.services.processes import Orchestrator, ProcessError

PY = sys.executable or "python3"


def wait_until(predicate, timeout=10.0, interval=0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class OrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.orch = Orchestrator()
        self.addCleanup(self.orch.shutdown)

    def test_start_and_capture_output(self):
        proc = self.orch.start(f"{PY} -c \"print('hello'); print('world')\"", cwd="/")
        self.assertTrue(wait_until(lambda: not proc.running))
        lines, cursor = proc.logs()
        self.assertEqual(lines, ["hello", "world"])
        self.assertEqual(cursor, 2)
        self.assertEqual(proc.returncode, 0)

    def test_stderr_is_merged_into_log(self):
        proc = self.orch.start(
            f"{PY} -c \"import sys; sys.stderr.write('oops\\n')\"", cwd="/"
        )
        self.assertTrue(wait_until(lambda: not proc.running))
        lines, _ = proc.logs()
        self.assertEqual(lines, ["oops"])

    def test_log_cursor_returns_only_new_lines(self):
        proc = self.orch.start(f"{PY} -c \"[print(i) for i in range(5)]\"", cwd="/")
        self.assertTrue(wait_until(lambda: not proc.running))
        self.assertTrue(wait_until(lambda: proc.logs()[1] == 5))
        _, cursor = proc.logs()
        lines, cursor2 = proc.logs(since=cursor)
        self.assertEqual(lines, [])
        self.assertEqual(cursor2, cursor)
        lines, _ = proc.logs(since=3)
        self.assertEqual(lines, ["3", "4"])

    def test_stop_terminates_long_running_process(self):
        proc = self.orch.start(f"{PY} -c \"import time; time.sleep(60)\"", cwd="/")
        self.assertTrue(proc.running)
        code = self.orch.stop(proc.id)
        self.assertFalse(proc.running)
        self.assertIsNotNone(code)
        self.assertNotEqual(code, 0)  # killed by signal

    def test_stop_kills_whole_process_group(self):
        # the shell spawns a child python; killing the group must reach it
        proc = self.orch.start(
            f"{PY} -c \"import time; print('up', flush=True); time.sleep(60)\" & wait",
            cwd="/",
        )
        self.assertTrue(wait_until(lambda: proc.logs()[0] == ["up"]))
        self.orch.stop(proc.id)
        self.assertTrue(wait_until(lambda: not proc.running))

    def test_failing_command_records_returncode(self):
        proc = self.orch.start(f"{PY} -c \"raise SystemExit(3)\"", cwd="/")
        self.assertTrue(wait_until(lambda: not proc.running))
        self.assertEqual(proc.returncode, 3)

    def test_start_rejects_empty_command_and_bad_cwd(self):
        with self.assertRaises(ProcessError):
            self.orch.start("", cwd="/")
        with self.assertRaises(ProcessError):
            self.orch.start("true", cwd="/definitely/not/a/dir")

    def test_list_filters_by_project(self):
        a = self.orch.start("true", cwd="/", project="alpha")
        self.orch.start("true", cwd="/", project="beta")
        snaps = self.orch.list(project="alpha")
        self.assertEqual([s["id"] for s in snaps], [a.id])
        self.assertEqual(len(self.orch.list()), 2)

    def test_running_for_reports_only_live_matching_kind(self):
        dev = self.orch.start(
            f"{PY} -c \"import time; time.sleep(60)\"",
            cwd="/", project="alpha", kind="dev",
        )
        done = self.orch.start("true", cwd="/", project="alpha", kind="task")
        self.assertTrue(wait_until(lambda: not done.running))
        live = self.orch.running_for("alpha", "dev")
        self.assertEqual([p.id for p in live], [dev.id])
        self.assertEqual(self.orch.running_for("alpha", "task"), [])

    def test_remove_refuses_running_process(self):
        proc = self.orch.start(f"{PY} -c \"import time; time.sleep(60)\"", cwd="/")
        with self.assertRaises(ProcessError):
            self.orch.remove(proc.id)
        self.orch.stop(proc.id)
        self.assertTrue(self.orch.remove(proc.id))
        self.assertIsNone(self.orch.get(proc.id))

    def test_stop_unknown_process_raises(self):
        with self.assertRaises(ProcessError):
            self.orch.stop("nope")

    def test_shutdown_stops_everything(self):
        procs = [
            self.orch.start(f"{PY} -c \"import time; time.sleep(60)\"", cwd="/")
            for _ in range(3)
        ]
        self.orch.shutdown()
        for proc in procs:
            self.assertFalse(proc.running)


if __name__ == "__main__":
    unittest.main()
