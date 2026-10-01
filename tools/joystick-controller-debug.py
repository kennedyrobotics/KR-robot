#!/usr/bin/env python3
"""SN2403 joystick controller debug / verification dashboard (full-screen, curses).

Shows everything the kernel exposes for the pad, live, and keeps a verification checklist:
  * device: name, node, bus (USB/Bluetooth), VID:PID, driver mode guess, rumble support
  * every axis: raw value, normalised value, bar, kernel range/fuzz/flat, min/max seen,
    and whether full travel has been reached in both directions
  * both sticks plotted in 2-D, with centre drift (resting offset) shown
  * D-pad and every button the device reports: live state, press count, verified tick
  * teleop preview: what tools/teleop.py would command from these inputs (mode, L/R PWM)
  * link health: event rate, time since last event (auto-sleep), disconnect/reconnect count
  * raw event log (last N events)

Keys:  q quit   r strong rumble   w weak rumble   c clear checklist/counters   p pause log

    joystick-controller-debug.py [--dev /dev/input/eventN] [--report FILE]
    joystick-controller-debug.py --info          # non-interactive capability dump (works over plain ssh)

On exit a verification report is printed (and written to --report if given).
"""

import argparse
import collections
import curses
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from krc import joystick as js  # noqa: E402
from krc.drive import Inputs, TeleopConfig, TeleopController  # noqa: E402

FULL_TRAVEL = 0.95          # |normalised| that counts as "reached the end stop"
STICK_PAIRS = [("Left stick", js.ABS_X, js.ABS_Y), ("Right stick", js.ABS_RX, js.ABS_RY)]
# Functions teleop relies on — highlighted in the checklist
TELEOP_ROLES = {js.BTN_TL: "deadman", js.BTN_START: "arm", js.BTN_EAST: "e-stop", js.BTN_MODE: "e-stop"}
TELEOP_AXES = {js.ABS_Y: "throttle", js.ABS_RX: "steer", js.ABS_RY: "tank R"}

KNOWN_DRIVERS = {(0x045E, None): "Xbox / XInput (xpad or hid-microsoft)",
                 (0x054C, None): "PlayStation (hid-playstation)",
                 (0x057E, None): "Nintendo Switch (hid-nintendo)"}


def key_name(code: int) -> str:
    return js.BTN_NAMES.get(code, f"KEY_{code:#x}")


def axis_name(code: int) -> str:
    return js.ABS_NAMES.get(code, f"ABS_{code:#x}")


def driver_guess(pad: js.Gamepad) -> str:
    for (vid, _), desc in KNOWN_DRIVERS.items():
        if pad.vendor == vid:
            return desc
    return "generic HID gamepad (check mode: PC/XInput recommended)"


class Tracker:
    """Everything observed since start (or last 'c')."""

    def __init__(self):
        self.reset()
        self.disconnects = 0
        self.connects = 0
        self.pad_info: dict = {}      # captured on connect so the exit report survives a dropped pad

    def reset(self):
        self.ax_min: dict[int, float] = {}
        self.ax_max: dict[int, float] = {}
        self.ax_rest: dict[int, float] = {}
        self.presses = collections.Counter()
        self.events = 0
        self.t0 = time.monotonic()
        self.last_event = None
        self.rate_window = collections.deque()

    def axis(self, code, v):
        self.ax_min[code] = min(self.ax_min.get(code, v), v)
        self.ax_max[code] = max(self.ax_max.get(code, v), v)

    def travel_ok(self, code, one_sided):
        lo, hi = self.ax_min.get(code, 0.0), self.ax_max.get(code, 0.0)
        return hi >= FULL_TRAVEL and (one_sided or lo <= -FULL_TRAVEL)

    def event(self, now):
        self.events += 1
        self.last_event = now
        self.rate_window.append(now)
        while self.rate_window and now - self.rate_window[0] > 1.0:
            self.rate_window.popleft()


def bar(v: float, width: int, one_sided: bool) -> str:
    if one_sided:
        n = int(round(v * width))
        return "[" + "#" * n + "." * (width - n) + "]"
    half = width // 2
    n = int(round(abs(v) * half))
    left = "." * (half - n) + "#" * n if v < 0 else "." * half
    right = "#" * n + "." * (half - n) if v > 0 else "." * half
    return "[" + left + "|" + right + "]"


def stick_plot(x: float, y: float, w: int = 15, h: int = 7) -> list[str]:
    cx, cy = int(round((x + 1) / 2 * (w - 1))), int(round((y + 1) / 2 * (h - 1)))
    rows = []
    for r in range(h):
        line = ""
        for c in range(w):
            if r == cy and c == cx:
                line += "@"
            elif r == h // 2 and c == w // 2:
                line += "+"
            elif r == h // 2:
                line += "-"
            elif c == w // 2:
                line += "|"
            else:
                line += " "
        rows.append("|" + line + "|")
    return ["+" + "-" * w + "+"] + rows + ["+" + "-" * w + "+"]


def dpad_glyph(hx: float, hy: float) -> list[str]:
    up, down, left, right = hy < -0.5, hy > 0.5, hx < -0.5, hx > 0.5
    return ["   " + ("[^]" if up else " ^ ") + "   ",
            ("[<]" if left else " < ") + " + " + ("[>]" if right else " > "),
            "   " + ("[v]" if down else " v ") + "   "]


class Dashboard:
    def __init__(self, scr, dev, tracker: Tracker):
        self.scr = scr
        self.dev = dev
        self.t = tracker
        self.pad: js.Gamepad | None = None
        self.log = collections.deque(maxlen=200)
        self.paused = False
        self.msg = ""
        self.teleop = TeleopController(TeleopConfig())
        self.prev = time.monotonic()
        self.next_reconnect = 0.0
        self.has_color = False

    # -- drawing helpers -----------------------------------------------------
    def put(self, y, x, text, attr=0):
        h, w = self.scr.getmaxyx()
        if 0 <= y < h - 1 and x < w:
            try:
                self.scr.addnstr(y, x, text, max(0, w - x - 1), attr)
            except curses.error:
                pass

    def col(self, n):
        return curses.color_pair(n) if self.has_color else 0

    # -- main loop -----------------------------------------------------------
    def run(self):
        curses.curs_set(0)
        self.scr.nodelay(True)
        if curses.has_colors():
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_GREEN, -1)
            curses.init_pair(2, curses.COLOR_RED, -1)
            curses.init_pair(3, curses.COLOR_YELLOW, -1)
            curses.init_pair(4, curses.COLOR_CYAN, -1)
            self.has_color = True
        while True:
            now = time.monotonic()
            self.connect(now)
            self.poll(now)
            if not self.keys():
                break
            self.draw(now)
            time.sleep(0.02)

    def connect(self, now):
        if self.pad is not None or now < self.next_reconnect:
            return
        try:
            self.pad = js.Gamepad(self.dev)
            self.t.connects += 1
            p = self.pad
            self.t.pad_info = {"device": p.name, "node": p.path, "id": f"{p.vendor:04x}:{p.product:04x}",
                               "bus": js.BUS_NAMES.get(p.bustype, p.bustype), "rumble": p.can_rumble,
                               "axes": {c: (i.one_sided, axis_name(c)) for c, i in p.axis_info.items()},
                               "keys": sorted(p.keys)}
            for code in self.pad.axis_info:
                self.t.ax_rest.setdefault(code, self.pad.state.axis(code))
            self.msg = f"connected: {self.pad.name}"
        except OSError:
            self.next_reconnect = now + 1.0

    def poll(self, now):
        if self.pad is None:
            return
        try:
            events = self.pad.poll(0.0)
        except OSError as e:
            self.t.disconnects += 1
            self.msg = f"LINK LOST ({e.strerror or e}) — teleop would SAFE-STOP here; waiting to reconnect"
            self.pad.close()
            self.pad = None
            self.next_reconnect = now + 1.0
            return
        for etype, code, value in events:
            self.t.event(now)
            if etype == js.EV_KEY and value == 1:
                self.t.presses[code] += 1
            if not self.paused:
                name = key_name(code) if etype == js.EV_KEY else axis_name(code) if etype == js.EV_ABS else str(code)
                self.log.append(f"{now - self.t.t0:8.3f}  {js.EV_NAMES.get(etype, etype):3}  {name:10} {value}")
        for code in self.pad.axis_info:
            self.t.axis(code, self.pad.state.axis(code))

    def keys(self) -> bool:
        ch = self.scr.getch()
        if ch in (ord("q"), ord("Q"), 27):
            return False
        if ch in (ord("r"), ord("w")) and self.pad is not None:
            try:
                strong, weak = (1.0, 0.0) if ch == ord("r") else (0.0, 1.0)
                self.pad.rumble(strong, weak, 400)
                self.msg = f"rumble: {'strong (left)' if strong else 'weak (right)'} motor, 400 ms"
            except OSError as e:
                self.msg = f"rumble failed: {e.strerror or e}"
        elif ch == ord("c"):
            self.t.reset()
            if self.pad:
                for code in self.pad.axis_info:
                    self.t.ax_rest[code] = self.pad.state.axis(code)
            self.msg = "checklist cleared — rest positions re-captured (hands off sticks)"
        elif ch == ord("p"):
            self.paused = not self.paused
        return True

    def draw(self, now):
        s = self.scr
        s.erase()
        h, w = s.getmaxyx()
        pad = self.pad
        y = 0
        self.put(y, 0, " KR-Robot joystick controller debug ", curses.A_REVERSE)
        self.put(y, 38, "q quit  r/w rumble  c clear  p pause log")
        y += 1
        if pad is None:
            self.put(y + 1, 2, "No gamepad. Waiting... (plug in USB, or pair in PC mode over Bluetooth)",
                     self.col(2) | curses.A_BOLD)
            self.put(y + 3, 2, self.msg, self.col(3))
            self.put(y + 4, 2, f"connects={self.t.connects}  disconnects={self.t.disconnects}")
            s.refresh()
            return

        bus = js.BUS_NAMES.get(pad.bustype, f"bus {pad.bustype:#x}")
        idle = (now - self.t.last_event) if self.t.last_event else now - self.t.t0
        self.put(y, 0, f" {pad.name}  [{pad.path}]  {bus} {pad.vendor:04x}:{pad.product:04x} v{pad.version:x}")
        y += 1
        rumble = "yes" if pad.can_rumble else ("no (FF absent)" if js.FF_RUMBLE not in pad.ff else "no (not writable)")
        self.put(y, 0, f" mode: {driver_guess(pad)}   rumble: {rumble}")
        y += 1
        idle_attr = self.col(3) if idle > 60 else 0
        self.put(y, 0, f" events={self.t.events}  rate={len(self.t.rate_window):3d}/s  idle={idle:5.1f}s"
                       f"  connects={self.t.connects} disconnects={self.t.disconnects}", idle_attr)
        y += 2

        # --- axes -----------------------------------------------------------
        self.put(y, 0, " AXIS        raw     norm  bar                         range          fuzz flat  seen min/max   travel", curses.A_BOLD)
        y += 1
        for code in sorted(pad.axis_info):
            info = pad.axis_info[code]
            v = pad.state.axis(code)
            ok = self.t.travel_ok(code, info.one_sided)
            role = TELEOP_AXES.get(code, "")
            line = (f" {axis_name(code):9} {pad.raw_axes.get(code, 0):7d} {v:+6.2f}  {bar(v, 24, info.one_sided)} "
                    f"{info.minimum:6d}..{info.maximum:<6d} {info.fuzz:4d} {info.flat:4d}  "
                    f"{self.t.ax_min.get(code, 0):+5.2f}/{self.t.ax_max.get(code, 0):+5.2f}  "
                    f"{'OK' if ok else '--'} {role}")
            self.put(y, 0, line, self.col(1) if ok else 0)
            y += 1
        y += 1

        # --- sticks, d-pad, teleop -------------------------------------------
        top = y
        x = 1
        for label, ax, ay in STICK_PAIRS:
            if ax in pad.axis_info and ay in pad.axis_info:
                sx, sy = pad.state.axis(ax), pad.state.axis(ay)
                self.put(top, x, label, curses.A_BOLD)
                for i, row in enumerate(stick_plot(sx, sy)):
                    self.put(top + 1 + i, x, row)
                dx = self.t.ax_rest.get(ax, 0.0)
                dy = self.t.ax_rest.get(ay, 0.0)
                drift = max(abs(dx), abs(dy))
                self.put(top + 10, x, f"rest drift {drift:.3f}", self.col(2) if drift > 0.08 else self.col(1))
                x += 20
        if js.ABS_HAT0X in pad.axis_info:
            self.put(top, x, "D-pad", curses.A_BOLD)
            for i, row in enumerate(dpad_glyph(pad.state.axis(js.ABS_HAT0X), pad.state.axis(js.ABS_HAT0Y))):
                self.put(top + 2 + i, x, row)
            x += 14

        # teleop preview — exactly the mapping tools/teleop.py uses
        st = pad.state
        inputs = Inputs(lx=st.axis(js.ABS_X), ly=st.axis(js.ABS_Y), rx=st.axis(js.ABS_RX), ry=st.axis(js.ABS_RY),
                        deadman=st.button(js.BTN_TL), arm=st.button(js.BTN_START),
                        estop=st.button(js.BTN_EAST) or st.button(js.BTN_MODE))
        dt, self.prev = now - self.prev, now
        left, right = self.teleop.update(inputs, dt)
        mode_col = {"ARMED": 1, "ESTOP": 2}.get(self.teleop.mode.value, 3)
        self.put(top, x, "Teleop preview (no motors)", curses.A_BOLD)
        self.put(top + 1, x, f"mode   {self.teleop.mode.value}", self.col(mode_col) | curses.A_BOLD)
        self.put(top + 2, x, f"reason {self.teleop.reason}")
        self.put(top + 3, x, f"deadman(LB) {'HELD' if inputs.deadman else 'off '}")
        self.put(top + 4, x, f"L PWM {left:+5d} {bar(left / self.teleop.cfg.max_pwm, 16, False)}")
        self.put(top + 5, x, f"R PWM {right:+5d} {bar(right / self.teleop.cfg.max_pwm, 16, False)}")
        self.put(top + 6, x, "START arm | hold LB | B/HOME e-stop")
        self.put(top + 7, x, f"(R inverted={self.teleop.cfg.invert_right}, max={self.teleop.cfg.max_pwm})")
        y = top + 12

        # --- buttons ---------------------------------------------------------
        self.put(y, 0, " BUTTONS (state / presses / verified)", curses.A_BOLD)
        y += 1
        codes = sorted(pad.keys)
        col_w = 26
        per_row = max(1, (w - 1) // col_w)
        for i, code in enumerate(codes):
            r, c = divmod(i, per_row)
            pressed = st.button(code)
            n = self.t.presses[code]
            role = TELEOP_ROLES.get(code, "")
            txt = f"{'##' if pressed else '  '} {key_name(code):7} {n:3d} {'OK' if n else '--'} {role}"
            attr = curses.A_REVERSE if pressed else (self.col(1) if n else 0)
            self.put(y + r, 1 + c * col_w, txt, attr)
        y += (len(codes) + per_row - 1) // per_row + 1

        verified = sum(1 for c in codes if self.t.presses[c]) + sum(
            1 for c, i in pad.axis_info.items() if self.t.travel_ok(c, i.one_sided))
        total = len(codes) + len(pad.axis_info)
        self.put(y, 0, f" VERIFIED {verified}/{total}", (self.col(1) if verified == total else self.col(3)) | curses.A_BOLD)
        self.put(y, 20, self.msg, self.col(4))
        y += 2

        # --- event log -------------------------------------------------------
        self.put(y, 0, f" EVENT LOG{' (paused)' if self.paused else ''}", curses.A_BOLD)
        y += 1
        room = h - y - 1
        for line in list(self.log)[-room:] if room > 0 else []:
            self.put(y, 1, line)
            y += 1
        s.refresh()


def report(t: Tracker) -> str:
    info = t.pad_info
    lines = ["KR-Robot joystick verification report", time.strftime("  %Y-%m-%d %H:%M:%S")]
    if not info:
        return "\n".join(lines + ["  no gamepad was connected"])
    for k in ("device", "node", "id", "bus", "rumble"):
        lines.append(f"  {k}: {info[k]}")
    lines.append(f"  events={t.events} connects={t.connects} disconnects={t.disconnects}")
    lines.append("Axes (full travel):")
    for code, (one_sided, name) in sorted(info["axes"].items()):
        ok = t.travel_ok(code, one_sided)
        lines.append(f"  [{'x' if ok else ' '}] {name:10} min {t.ax_min.get(code, 0):+.2f} max {t.ax_max.get(code, 0):+.2f}"
                     f" rest {t.ax_rest.get(code, 0):+.3f}")
    lines.append("Buttons (pressed at least once):")
    for code in info["keys"]:
        lines.append(f"  [{'x' if t.presses[code] else ' '}] {key_name(code):8} x{t.presses[code]}")
    return "\n".join(lines)


def info_dump(dev) -> int:
    pads = js.find_gamepads() if dev is None else [(dev, js.device_name(dev))]
    if not pads:
        print("No gamepad found in /dev/input/event*.")
        return 1
    for path, _ in pads:
        with js.Gamepad(path, grab=False) as pad:
            print(f"{pad.path}: {pad.name}")
            print(f"  bus={js.BUS_NAMES.get(pad.bustype, pad.bustype)} id={pad.vendor:04x}:{pad.product:04x} "
                  f"version={pad.version:x}  mode guess: {driver_guess(pad)}")
            print(f"  rumble: {'yes' if pad.can_rumble else 'no'}   writable: {pad.writable}")
            print("  axes:")
            for code, i in sorted(pad.axis_info.items()):
                print(f"    {axis_name(code):10} {i.minimum:>7}..{i.maximum:<7} fuzz {i.fuzz:<4} flat {i.flat:<5}"
                      f" {'trigger' if i.one_sided else 'centred'}  now {pad.state.axis(code):+.2f}")
            print("  buttons: " + " ".join(key_name(c) for c in sorted(pad.keys)))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dev", help="/dev/input/eventN (default: first gamepad found)")
    ap.add_argument("--report", help="also write the verification report to this file")
    ap.add_argument("--info", action="store_true", help="print capabilities and exit (no curses)")
    a = ap.parse_args()
    if a.info:
        return info_dump(a.dev)
    if not sys.stdout.isatty():
        print("Needs an interactive terminal (use: ssh -t ...). For a plain dump use --info.")
        return 2

    tracker = Tracker()

    def _run(scr):
        dash = Dashboard(scr, a.dev, tracker)
        try:
            dash.run()
        finally:
            if dash.pad is not None:
                dash.pad.close()

    try:
        curses.wrapper(_run)
    except KeyboardInterrupt:
        pass
    except curses.error as e:
        print(f"terminal error: {e} (TERM={os.environ.get('TERM', 'unset')}). "
              "Try: TERM=xterm-256color, or use --info.")
        return 2
    text = report(tracker)
    print(text)
    if a.report:
        with open(a.report, "w") as f:
            f.write(text + "\n")
        print(f"\nreport written to {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
