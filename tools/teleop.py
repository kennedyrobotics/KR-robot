#!/usr/bin/env python3
"""Joystick teleop: SN2403 -> Yahboom board, open-loop PWM, with deadman / e-stop / watchdog.

Controls (XInput layout):
    START         arm (LB released, sticks centred)
    hold LB       deadman — the robot only moves while held
    left stick Y  throttle        right stick X  steer          (--tank: left Y / right Y)
    B or HOME     e-stop (latched; re-arm with START)

    teleop.py [--max-pwm 1800] [--tank] [--no-invert-right] [--dry-run]

--dry-run prints commands without opening the motor port — use it to check mixing first.
"""

import argparse
import logging
import os
import signal
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from krc import joystick as js  # noqa: E402
from krc import yahboom  # noqa: E402
from krc.drive import Inputs, TeleopConfig, TeleopController  # noqa: E402

log = logging.getLogger("teleop")
RATE_HZ = 50


class DryRunMotors:
    port = "(dry-run)"
    link_ok = True

    def set_pwm(self, *ch):
        pass

    def stop(self):
        pass

    def close(self):
        pass


def read_inputs(pad: js.Gamepad) -> Inputs:
    s = pad.state
    return Inputs(lx=s.axis(js.ABS_X), ly=s.axis(js.ABS_Y), rx=s.axis(js.ABS_RX), ry=s.axis(js.ABS_RY),
                  deadman=s.button(js.BTN_TL), arm=s.button(js.BTN_START),
                  estop=s.button(js.BTN_EAST) or s.button(js.BTN_MODE))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=yahboom.DEFAULT_PORT)
    ap.add_argument("--dev", help="gamepad /dev/input/eventN (default: auto)")
    ap.add_argument("--max-pwm", type=int, default=TeleopConfig.max_pwm)
    ap.add_argument("--tank", action="store_true")
    ap.add_argument("--invert-left", action="store_true")
    ap.add_argument("--no-invert-right", action="store_true")
    ap.add_argument("--pwm-cmd", default=yahboom.PWM_CMD)
    ap.add_argument("--no-init", action="store_true", help="skip the $mtype/$deadzone init sequence")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    cfg = TeleopConfig(max_pwm=a.max_pwm, tank=a.tank, invert_left=a.invert_left,
                       invert_right=not a.no_invert_right)
    ctl = TeleopController(cfg)

    if a.dry_run:
        motors = DryRunMotors()
    else:
        motors = yahboom.YahboomMotorController(a.port, max_pwm=a.max_pwm, pwm_cmd=a.pwm_cmd)
        motors.open()
        if not a.no_init:
            motors.configure(yahboom.PROFILE_33GB520_NO_ENCODER)
        motors.stop()

    stop_requested = False

    def _sig(*_):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    pad = None
    next_reconnect = 0.0
    last_status = None
    period = 1.0 / RATE_HZ
    t_prev = time.monotonic()
    try:
        while not stop_requested:
            now = time.monotonic()
            dt, t_prev = now - t_prev, now

            if pad is None and now >= next_reconnect:
                try:
                    pad = js.Gamepad(a.dev)
                    log.info("gamepad connected: %s (%s) — press START to arm", pad.name, pad.path)
                except OSError:
                    next_reconnect = now + 1.0

            inputs = Inputs(link_ok=False)
            if pad is not None:
                try:
                    pad.poll(0.0)
                    inputs = read_inputs(pad)
                except OSError as e:
                    log.warning("gamepad link lost (%s) — SAFE STOP", e)
                    pad.close()
                    pad = None
                    next_reconnect = now + 1.0

            if not motors.link_ok:
                log.error("motor serial link lost — SAFE STOP and exit")
                break

            left, right = ctl.update(inputs, dt)
            motors.set_pwm(*ctl.channels(left, right))

            # mode changes always logged; per-tick outputs only in dry-run / verbose
            status = (ctl.mode, ctl.reason, (left, right) if (a.dry_run or a.verbose) else None)
            if status != last_status:
                log.info("%-9s L=%+5d R=%+5d  (%s)", ctl.mode.value, left, right, ctl.reason)
            last_status = status

            time.sleep(max(0.0, period - (time.monotonic() - now)))
    finally:
        motors.stop()
        motors.close()
        if pad is not None:
            pad.close()
        log.info("teleop exit — motors stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
