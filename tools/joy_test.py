#!/usr/bin/env python3
"""SN2403 / gamepad bench test — list pads and show live, normalised axes and buttons.

    joy_test.py --list          # every evdev gamepad and its axis ranges
    joy_test.py [--dev PATH]    # live view; Ctrl-C to quit. Survives unplug/replug.
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from krc import joystick as js  # noqa: E402


def list_pads() -> int:
    pads = js.find_gamepads()
    if not pads:
        print("No gamepad found. Check: lsusb, dmesg | tail, ls -l /dev/input/by-id/")
        return 1
    for path, name in pads:
        print(f"{path}  {name}")
        with js.Gamepad(path, grab=False) as pad:
            for code, info in sorted(pad.axis_info.items()):
                kind = "trigger" if info.one_sided else "centred"
                print(f"    {js.ABS_NAMES.get(code, hex(code)):10s} {info.minimum:>7}..{info.maximum:<7} {kind}")
    return 0


def live(dev: str | None) -> int:
    while True:
        try:
            pad = js.Gamepad(dev)
        except OSError as e:
            print(f"\rwaiting for gamepad ({e.strerror}) ...", end="", flush=True)
            time.sleep(1.0)
            continue
        print(f"\nConnected: {pad.name} ({pad.path})")
        try:
            while True:
                pad.poll(0.05)
                s = pad.state
                axes = " ".join(f"{js.ABS_NAMES.get(c, hex(c))}={s.axis(c):+.2f}" for c in sorted(pad.axis_info))
                btns = " ".join(js.BTN_NAMES.get(c, hex(c)) for c, v in sorted(s.buttons.items()) if v)
                print(f"\r{axes} | {btns:<24}", end="", flush=True)
        except OSError as e:
            print(f"\nLINK LOST: {e} — this is where teleop would safe-stop")
            pad.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dev", help="/dev/input/eventN (default: first gamepad found)")
    a = ap.parse_args()
    try:
        return list_pads() if a.list else live(a.dev)
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    sys.exit(main())
