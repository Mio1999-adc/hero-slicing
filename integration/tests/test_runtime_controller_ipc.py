"""Focused tests for PPO child stdout framing and long-processing heartbeats."""
from __future__ import annotations

import io
import unittest

from integration.learning.runtime_controller import serve_controller


class FakeController:
    def __init__(self, delay: float = 0.0, fail: Exception | None = None):
        self.delay = delay
        self.fail = fail
        self.calls = []

    def process(self, sequence, sim_end_ns, csv_path, node_ids, initial):
        import time
        self.calls.append((sequence, sim_end_ns, tuple(node_ids)))
        if self.delay:
            time.sleep(self.delay)
        if self.fail:
            raise self.fail
        return {node: (7, (0.2, 0.3, 0.5)) for node in node_ids}


class BrokenAfterReady:
    def __init__(self):
        self.lines = []

    def write(self, line):
        if self.lines:
            raise BrokenPipeError(32, "pipe reader closed")
        self.lines.append(line)

    def flush(self):
        pass


class RuntimeControllerIpcTests(unittest.TestCase):
    def request(self, sequence=1, ids="101"):
        ratios = ";".join(f"{node}:0.34,0.33,0.33" for node in ids.split(","))
        return f"SAMPLE\t{sequence}\t1000000000\t/tmp/measurements.csv\t{ids}\t{ratios}\n"

    def test_normal_exchange_and_multi_gnb_framing(self):
        fake = FakeController()
        stdin = io.StringIO(self.request(13, "101,102") + "STOP\n")
        stdout, stderr = io.StringIO(), io.StringIO()
        self.assertEqual(serve_controller(fake, stdin, stdout, stderr), 0)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(lines[0], "READY")
        self.assertIn("ACTION\t101:7:0.20000000000000001,0.29999999999999999,0.5;102:7:0.20000000000000001,0.29999999999999999,0.5", lines)
        self.assertEqual(fake.calls, [(13, 1_000_000_000, (101, 102))])
        self.assertIn("event=request_complete request_id=13", stderr.getvalue())

    def test_slow_update_emits_heartbeats_before_action(self):
        fake = FakeController(delay=0.06)
        stdout = io.StringIO()
        self.assertEqual(serve_controller(fake, io.StringIO(self.request() + "STOP\n"), stdout,
                                          io.StringIO(), heartbeat_seconds=0.01), 0)
        lines = stdout.getvalue().splitlines()
        self.assertTrue(any(line.startswith("BUSY\t1\tprocessing\t") for line in lines))
        self.assertTrue(lines[-1].startswith("ACTION\t"))

    def test_controller_error_is_framed_and_loop_remains_usable(self):
        fake = FakeController(fail=RuntimeError("slow optimizer rejected transition"))
        stdout = io.StringIO()
        self.assertEqual(serve_controller(fake, io.StringIO(self.request() + self.request(2) + "STOP\n"),
                                          stdout, io.StringIO(), heartbeat_seconds=0.01), 0)
        lines = stdout.getvalue().splitlines()
        self.assertTrue(any(line.startswith("ERROR\tRuntimeError:") for line in lines))
        self.assertEqual(sum(line.startswith("ERROR\t") for line in lines), 2)

    def test_broken_pipe_does_not_trigger_second_write_or_traceback(self):
        stderr = io.StringIO()
        result = serve_controller(FakeController(), io.StringIO(self.request()),
                                  BrokenAfterReady(), stderr, heartbeat_seconds=0.01)
        self.assertEqual(result, 1)
        self.assertIn("event=stdout_closed phase=response", stderr.getvalue())
        self.assertNotIn("Traceback", stderr.getvalue())

    def test_clean_shutdown_after_eof_or_stop(self):
        for input_text in ("", "STOP\n"):
            stdout = io.StringIO()
            self.assertEqual(serve_controller(FakeController(), io.StringIO(input_text), stdout,
                                              io.StringIO()), 0)
            self.assertEqual(stdout.getvalue().splitlines(), ["READY"])


if __name__ == "__main__":
    unittest.main()
