"""Hardware-free unit tests: framing, report parsing, axis normalisation, mixing, safety.

Run on Windows or the board:  python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from krc import drive  # noqa: E402
from krc.drive import Inputs, Mode, TeleopConfig, TeleopController  # noqa: E402
from krc.yahboom import Telemetry, format_cmd, parse_frames, parse_report  # noqa: E402

DT = 0.02


class TestYahboomFraming(unittest.TestCase):
    def test_format(self):
        self.assertEqual(format_cmd("pwm", 100, -200, 0, 0), b"$pwm:100,-200,0,0#")
        self.assertEqual(format_cmd("mtype", 4), b"$mtype:4#")
        self.assertEqual(format_cmd("wdiameter", 67.0), b"$wdiameter:67.00#")

    def test_parse_frames_split_and_partial(self):
        buf = bytearray(b"junk$MAll:1, 2, 3, 4#$MTEP:5,6")
        self.assertEqual(parse_frames(buf), ["MAll:1, 2, 3, 4"])
        self.assertEqual(bytes(buf), b"$MTEP:5,6")
        buf.extend(b",7,8#")
        self.assertEqual(parse_frames(buf), ["MTEP:5,6,7,8"])
        self.assertEqual(buf, bytearray())

    def test_parse_report(self):
        t = Telemetry()
        self.assertTrue(parse_report("MAll:10, -20, 30, 40", t))
        self.assertEqual(t.total_pulses, [10, -20, 30, 40])
        self.assertTrue(parse_report("MSPD:1.5,0,0,-2.25", t))
        self.assertEqual(t.speed_mm_s, [1.5, 0.0, 0.0, -2.25])
        self.assertFalse(parse_report("ok", t))
        self.assertFalse(parse_report("MAll:x,y", t))


class TestAxis(unittest.TestCase):
    def setUp(self):
        # joystick.py imports fcntl (Linux-only); skip the axis tests on Windows
        try:
            from krc import joystick
        except ImportError:
            self.skipTest("Linux-only module")
        self.js = joystick

    def test_xpad_stick_and_trigger(self):
        stick = self.js.AxisInfo(self.js.ABS_X, -32768, 32767, 128)
        self.assertAlmostEqual(stick.normalise(32767), 1.0)
        self.assertAlmostEqual(stick.normalise(-32768), -1.0)
        trig = self.js.AxisInfo(self.js.ABS_Z, 0, 255, 0)
        self.assertTrue(trig.one_sided)
        self.assertAlmostEqual(trig.normalise(255), 1.0)

    def test_ps4_stick_is_centred(self):
        stick = self.js.AxisInfo(self.js.ABS_Y, 0, 255, 0)
        self.assertFalse(stick.one_sided)
        self.assertAlmostEqual(stick.normalise(0), -1.0)

    def test_ioctl_numbers_match_linux_headers(self):
        # values from <linux/input.h> on 64-bit Linux
        self.assertEqual(self.js._FF_EFFECT.size, 48)
        self.assertEqual(self.js.EVIOCSFF, 0x40304580)
        self.assertEqual(self.js.EVIOCGID, 0x80084502)
        self.assertEqual(self.js.EVIOCGABS(self.js.ABS_X), 0x80184540)
        self.assertEqual(self.js._EVENT.size, 24)

    def test_xbox_bt_right_stick_on_z_is_centred(self):
        self.assertFalse(self.js.AxisInfo(self.js.ABS_Z, 0, 65535, 0).one_sided)


class TestMixing(unittest.TestCase):
    def test_deadband(self):
        self.assertEqual(drive.deadband(0.05, 0.08), 0.0)
        self.assertAlmostEqual(drive.deadband(1.0, 0.08), 1.0)
        self.assertAlmostEqual(drive.deadband(-1.0, 0.08), -1.0)

    def test_arcade(self):
        self.assertEqual(drive.arcade_mix(1.0, 0.0), (1.0, 1.0))
        self.assertEqual(drive.arcade_mix(0.0, 1.0), (1.0, -1.0))
        l, r = drive.arcade_mix(1.0, 1.0)
        self.assertEqual((l, r), (1.0, 0.0))


def run(ctl, i, n=200):
    out = (0, 0)
    for _ in range(n):
        out = ctl.update(i, DT)
    return out


class TestSafety(unittest.TestCase):
    def setUp(self):
        self.cfg = TeleopConfig(max_pwm=1000, invert_right=False, expo=0.0)
        self.ctl = TeleopController(self.cfg)

    def arm(self):
        self.ctl.update(Inputs(arm=True), DT)
        self.ctl.update(Inputs(), DT)
        self.assertEqual(self.ctl.mode, Mode.ARMED)

    def test_starts_disarmed_and_ignores_sticks(self):
        self.assertEqual(run(self.ctl, Inputs(ly=-1.0, deadman=True)), (0, 0))
        self.assertEqual(self.ctl.mode, Mode.DISARMED)

    def test_arm_refused_with_deadman_or_stick(self):
        self.ctl.update(Inputs(arm=True, deadman=True), DT)
        self.assertEqual(self.ctl.mode, Mode.DISARMED)
        self.ctl.update(Inputs(), DT)
        self.ctl.update(Inputs(arm=True, ly=-0.5), DT)
        self.assertEqual(self.ctl.mode, Mode.DISARMED)

    def test_drive_needs_deadman(self):
        self.arm()
        self.assertEqual(run(self.ctl, Inputs(ly=-1.0)), (0, 0))
        self.assertEqual(run(self.ctl, Inputs(ly=-1.0, deadman=True)), (1000, 1000))
        # releasing the deadman stops instantly (no slew on the way down)
        self.assertEqual(self.ctl.update(Inputs(ly=-1.0), DT), (0, 0))

    def test_slew_limits_ramp_up(self):
        self.arm()
        l, _ = self.ctl.update(Inputs(ly=-1.0, deadman=True), DT)
        self.assertLessEqual(l, round(self.cfg.slew_per_s * DT * 1000) + 1)

    def test_estop_latches_until_rearm(self):
        self.arm()
        run(self.ctl, Inputs(ly=-1.0, deadman=True))
        self.assertEqual(self.ctl.update(Inputs(ly=-1.0, deadman=True, estop=True), DT), (0, 0))
        self.assertEqual(run(self.ctl, Inputs(ly=-1.0, deadman=True)), (0, 0))
        self.assertEqual(self.ctl.mode, Mode.ESTOP)
        self.arm()

    def test_link_loss_disarms(self):
        self.arm()
        run(self.ctl, Inputs(ly=-1.0, deadman=True))
        self.assertEqual(self.ctl.update(Inputs(link_ok=False), DT), (0, 0))
        self.assertEqual(self.ctl.mode, Mode.DISARMED)
        # link back, sticks still held: must not resume without re-arm
        self.assertEqual(run(self.ctl, Inputs(ly=-1.0, deadman=True)), (0, 0))

    def test_estop_survives_link_loss(self):
        self.ctl.force_estop("gui")
        run(self.ctl, Inputs(link_ok=False), n=5)
        self.assertEqual(self.ctl.mode, Mode.ESTOP)

    def test_inversion_and_channels(self):
        ctl = TeleopController(TeleopConfig(max_pwm=1000, invert_right=True, expo=0.0))
        ctl.update(Inputs(arm=True), DT); ctl.update(Inputs(), DT)
        l, r = run(ctl, Inputs(ly=-1.0, deadman=True))
        self.assertEqual((l, r), (1000, -1000))
        self.assertEqual(ctl.channels(l, r), [1000, -1000, 0, 0])


if __name__ == "__main__":
    unittest.main()
