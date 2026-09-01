import threading
import time
import unittest

from daily_log.idle_worker import IdleWorker


class IdleWorkerTest(unittest.TestCase):
    def test_runs_once_after_changes_stop(self):
        ran = threading.Event()
        calls = []
        worker = IdleWorker(lambda: (calls.append("backup"), ran.set()), lambda: (True, 0.03))
        worker.start()
        worker.notify()
        time.sleep(0.015)
        worker.notify()
        self.assertTrue(ran.wait(0.5))
        worker.stop()
        self.assertEqual(calls, ["backup"])

    def test_disabled_worker_does_nothing(self):
        ran = threading.Event()
        worker = IdleWorker(ran.set, lambda: (False, 0.01))
        worker.start()
        worker.notify()
        time.sleep(0.05)
        worker.stop()
        self.assertFalse(ran.is_set())


if __name__ == "__main__":
    unittest.main()
