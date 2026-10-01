#!/usr/bin/env python3
"""Yahboom motor board bench probe — Phase 0 protocol discovery and motor checks.

    yahboom_probe.py listen [-t 10]                 # print every frame the board sends
    yahboom_probe.py raw '$upload:1,0,0#' [-t 3]    # send any frame, show the reply
    yahboom_probe.py init [--profile 33gb520|md520] # send the motor-type init sequence
    yahboom_probe.py pwm 1 600 [-d 1.5]             # spin ONE channel briefly, then stop
    yahboom_probe.py stop                           # all channels to zero

SAFETY: put the chassis on blocks (tracks off the ground) before any pwm test.
The pwm sub-command always sends zero afterwards, including on Ctrl-C.
"""

import argparse
import logging
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from krc import yahboom  # noqa: E402
from krc.yahboom import YahboomMotorController  # noqa: E402

PROFILES = {"33gb520": yahboom.PROFILE_33GB520_NO_ENCODER, "md520": yahboom.PROFILE_MD520Z30}


def dump(ctl: YahboomMotorController, seconds: float) -> None:
    seen_other = 0
    t_end = time.monotonic() + seconds
    last = 0.0
    while time.monotonic() < t_end:
        t = ctl.telemetry
        while seen_other < len(t.other):
            print(f"  RX (other): ${t.other[seen_other]}#")
            seen_other += 1
        if t.last_rx and t.last_rx != last:
            last = t.last_rx
            print(f"  total={t.total_pulses} 10ms={t.pulses_10ms} mm/s={t.speed_mm_s}")
        time.sleep(0.05)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", default=yahboom.DEFAULT_PORT)
    ap.add_argument("--pwm-cmd", default=yahboom.PWM_CMD, help="PWM keyword if the board rejects '$pwm:'")
    ap.add_argument("-v", "--verbose", action="store_true", help="log every TX/RX frame")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("listen"); p.add_argument("-t", type=float, default=10)
    p = sub.add_parser("raw"); p.add_argument("frame"); p.add_argument("-t", type=float, default=2)
    p = sub.add_parser("init"); p.add_argument("--profile", choices=PROFILES, default="33gb520")
    p = sub.add_parser("pwm")
    p.add_argument("channel", type=int, choices=[1, 2, 3, 4])
    p.add_argument("value", type=int, help="PWM counts, signed; capped at --max")
    p.add_argument("-d", "--duration", type=float, default=1.0)
    p.add_argument("--max", type=int, default=1200, help="safety cap (default 1200 ≈ 33 %%)")
    sub.add_parser("stop")
    a = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    max_pwm = getattr(a, "max", yahboom.PWM_FULL_SCALE)
    with YahboomMotorController(a.port, max_pwm=max_pwm, pwm_cmd=a.pwm_cmd) as ctl:
        if a.cmd == "listen":
            print(f"Listening on {ctl.port} for {a.t:.0f} s ...")
            dump(ctl, a.t)
        elif a.cmd == "raw":
            print(f"TX {a.frame}")
            ctl.send_raw(a.frame)
            dump(ctl, a.t)
        elif a.cmd == "init":
            prof = PROFILES[a.profile]
            print(f"Configuring {a.profile}: {prof}")
            ctl.configure(prof)
            dump(ctl, 1.0)
        elif a.cmd == "pwm":
            vals = [0, 0, 0, 0]
            vals[a.channel - 1] = a.value
            print(f"M{a.channel} -> {max(-a.max, min(a.max, a.value))} for {a.duration} s (Ctrl-C stops)")
            try:
                t_end = time.monotonic() + a.duration
                while time.monotonic() < t_end:
                    ctl.set_pwm(*vals)          # re-send at 10 Hz in case the board has a timeout
                    time.sleep(0.1)
            finally:
                ctl.stop()
                print("stopped")
        elif a.cmd == "stop":
            ctl.stop()
            print("all channels zero")
    return 0


if __name__ == "__main__":
    sys.exit(main())
