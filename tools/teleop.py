#!/usr/bin/env python3
"""Headless joystick teleop (used by krc-teleop.service). Same RobotCore as the desktop app.

Controls (XInput layout):
    START         arm (LB released, sticks centred)
    hold LB       deadman — the robot only moves while held
    left stick Y  throttle        right stick X  steer          (--tank: left Y / right Y)
    B or HOME     e-stop (latched; re-arm with START)

    teleop.py [--max-pwm 1800] [--tank] [--no-invert-right] [--dry-run]

Settings default to ~/.config/krc-robot/settings.json (shared with the desktop app);
command-line flags override them for this run only. Exits non-zero if the motor link
drops, so systemd restarts it (it comes back DISARMED).
"""

import argparse
import logging
import os
import signal
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from krc.core import RobotCore, Settings  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port")
    ap.add_argument("--dev", help="gamepad /dev/input/eventN (default: auto)")
    ap.add_argument("--max-pwm", type=int)
    ap.add_argument("--tank", action="store_true")
    ap.add_argument("--invert-left", action="store_true")
    ap.add_argument("--no-invert-right", action="store_true")
    ap.add_argument("--pwm-cmd")
    ap.add_argument("--no-init", action="store_true", help="skip the $mtype/$deadzone init sequence")
    ap.add_argument("--dry-run", action="store_true", help="no motor port; log what would be sent")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    s = Settings.load()
    s.port = a.port or s.port
    s.pwm_cmd = a.pwm_cmd or s.pwm_cmd
    s.init_on_connect = s.init_on_connect and not a.no_init
    if a.max_pwm is not None:
        s.teleop.max_pwm = a.max_pwm
    s.teleop.tank = a.tank or s.teleop.tank
    s.teleop.invert_left = a.invert_left or s.teleop.invert_left
    if a.no_invert_right:
        s.teleop.invert_right = False

    core = RobotCore(s, gamepad_dev=a.dev, dry_run=a.dry_run)
    stop = False

    def _sig(*_):
        nonlocal stop
        stop = True

    signal.signal(signal.SIGTERM, _sig)
    signal.signal(signal.SIGINT, _sig)

    core.start()
    time.sleep(0.5)
    rc = 0
    if not a.dry_run and core.snapshot()["motors"] is None:
        logging.error("motor board not available (%s) — exiting", core.motor_error or s.port)
        stop, rc = True, 1
    last = None
    while not stop:
        snap = core.snapshot()
        if not a.dry_run and snap["motors"] is None:
            logging.error("motor link lost — exiting so systemd restarts us DISARMED")
            rc = 1
            break
        if a.dry_run or a.verbose:
            cur = (snap["mode"], snap["out"])
            if cur != last:
                logging.info("%-9s L=%+5d R=%+5d  (%s)", snap["mode"], *snap["out"], snap["reason"])
                last = cur
        time.sleep(0.05)
    core.shutdown()
    logging.info("teleop exit — motors stopped")
    return rc


if __name__ == "__main__":
    sys.exit(main())
