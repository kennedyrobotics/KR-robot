"""RobotCore safety tests — no hardware (no gamepad, dry-run or absent motor board). Linux only."""

import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

try:
    from krc import core as core_mod
    from krc.core import RobotCore, Settings
    from krc.drive import Mode
except ImportError:          # krc.joystick needs fcntl
    core_mod = None


@unittest.skipIf(core_mod is None, "Linux-only")
class TestRobotCore(unittest.TestCase):
    def make(self, dry_run=True):
        s = Settings()
        s.auto_connect_motors = False
        c = RobotCore(s, gamepad_dev="/dev/null-no-such-pad", dry_run=dry_run)
        c.start()
        self.addCleanup(c.shutdown)
        time.sleep(0.1)
        return c

    def test_starts_disarmed_with_zero_output(self):
        c = self.make()
        sn = c.snapshot()
        self.assertEqual(sn["mode"], "DISARMED")
        self.assertEqual(sn["out"], (0, 0))
        self.assertGreater(sn["loop_hz"] or 50, 0)

    def test_gui_estop_latches(self):
        c = self.make()
        c.estop("test")
        time.sleep(0.1)
        self.assertEqual(c.snapshot()["mode"], "ESTOP")

    def test_bench_refused_without_motor_board(self):
        c = self.make(dry_run=False)
        self.assertIn("not connected", c.bench_pulse(1, 500, 0.5))

    def test_bench_refused_when_armed(self):
        c = self.make()
        with c._lock:
            c.ctl.mode = Mode.ARMED
        self.assertIn("ARMED", c.bench_pulse(1, 500, 0.5))

    def test_bench_pulse_runs_then_estop_cancels(self):
        c = self.make()
        self.assertIsNone(c.bench_pulse(1, 500, 2.0))
        time.sleep(0.1)
        self.assertEqual(c.snapshot()["out"][0], 500)
        c.estop("test")
        time.sleep(0.1)
        self.assertEqual(c.snapshot()["out"], (0, 0))
        self.assertFalse(c.snapshot()["bench"])

    def test_settings_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "s.json")
            s = Settings()
            s.teleop.max_pwm, s.pwm_cmd = 1234, "speed"
            s.save(p)
            t = Settings.load(p)
            self.assertEqual((t.teleop.max_pwm, t.pwm_cmd), (1234, "speed"))


if __name__ == "__main__":
    unittest.main()
