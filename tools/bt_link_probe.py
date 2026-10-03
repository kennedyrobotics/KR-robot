#!/usr/bin/env python3
"""Bluetooth gamepad link probe: is teleop jitter caused by late / missing pad reports?

Records, side by side, for --seconds while you drive:
  * every HID input report from the pad (/dev/hidraw*, read alongside krbot's evdev grab), timestamped
  * krbot's monitor feed (loop rate, mode changes, output, pad drops, watchdog trips)
  * radio once a second: BT RSSI + link quality (hcitool), Wi-Fi level (/proc/net/wireless)
then prints a summary and saves the raw data as JSON.

    sudo python3 ~/krc-robot/tools/bt_link_probe.py              # 120 s
    sudo python3 ~/krc-robot/tools/bt_link_probe.py --seconds 60

Needs root for /dev/hidraw* and hcitool. Read-only: nothing is sent to the pad or the robot.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import queue
import re
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from krc.monitor_client import MonitorClient  # noqa: E402

GAP_BINS_MS = (25, 50, 100, 250, 500)


def find_pad() -> tuple[str, str]:
    """(/dev/hidrawN, BT address) of the first 'Xbox Wireless Controller' / gamepad on Bluetooth."""
    for d in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        ev = Path(d, "device", "uevent").read_text()
        name = re.search(r"HID_NAME=(.*)", ev)
        uniq = re.search(r"HID_UNIQ=(.*)", ev)
        hid_id = re.search(r"HID_ID=(\w+):", ev)
        if hid_id and int(hid_id.group(1), 16) == 0x5 and name and re.search(r"xbox|controller|gamepad", name.group(1), re.I):
            return "/dev/" + os.path.basename(d), (uniq.group(1) if uniq else "")
    raise SystemExit("no Bluetooth gamepad hidraw device found - is the pad connected?")


def hci(*args: str) -> str:
    try:
        return subprocess.run(["hcitool", *args], capture_output=True, text=True, timeout=2).stdout
    except (subprocess.TimeoutExpired, OSError):
        return ""


def wifi_level() -> float | None:
    try:
        for line in Path("/proc/net/wireless").read_text().splitlines():
            if line.strip().startswith("wlan0:"):
                return float(line.split()[3].rstrip("."))
    except (OSError, ValueError, IndexError):
        pass
    return None


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(p / 100 * (len(xs) - 1))))]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seconds", type=float, default=120)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("run with sudo (needs /dev/hidraw* and hcitool)")

    dev, mac = find_pad()
    print(f"pad: {dev}  {mac or '(no address)'}   recording {a.seconds:.0f} s - drive normally, include some held-steady stick", flush=True)
    t0 = time.monotonic()
    end = t0 + a.seconds
    reports: list[tuple[float, bytes]] = []
    status: list[tuple[float, dict]] = []
    logs: list[tuple[float, str]] = []
    radio: list[dict] = []
    stop = threading.Event()

    def read_hid() -> None:
        fd = os.open(dev, os.O_RDONLY)
        try:
            while not stop.is_set():
                try:
                    data = os.read(fd, 128)
                except OSError:          # pad dropped
                    reports.append((time.monotonic() - t0, b"<LOST>"))
                    return
                reports.append((time.monotonic() - t0, data))
        finally:
            os.close(fd)

    def read_radio() -> None:
        while not stop.is_set():
            t = time.monotonic() - t0
            rssi = re.search(r"RSSI return value:\s*(-?\d+)", hci("rssi", mac)) if mac else None
            lq = re.search(r"Link quality:\s*(\d+)", hci("lq", mac)) if mac else None
            radio.append({"t": round(t, 2), "bt_rssi": int(rssi.group(1)) if rssi else None,
                          "bt_lq": int(lq.group(1)) if lq else None, "wifi_dbm": wifi_level()})
            stop.wait(1.0)

    mc = MonitorClient()
    mc.start()
    threads = [threading.Thread(target=f, daemon=True) for f in (read_hid, read_radio)]
    for th in threads:
        th.start()
    next_tick = t0 + 10
    while time.monotonic() < end:
        try:
            m = mc.messages.get(timeout=0.2)
        except queue.Empty:
            m = None
        now = time.monotonic()
        if m and m.get("type") == "status":
            status.append((now - t0, m))
        elif m and m.get("type") == "log":
            logs.append((now - t0, m.get("line", "")))
        if now >= next_tick:
            print(f"  {now - t0:5.0f} s  reports {len(reports)}", flush=True)
            next_tick += 10
    stop.set()
    mc.stop()

    # ---------------------------------------------------------------- analysis
    out: list[str] = []
    p = out.append
    hid = [(t, d) for t, d in reports if d != b"<LOST>"]
    lost = [t for t, d in reports if d == b"<LOST>"]
    p(f"=== Bluetooth HID reports ({dev}, {a.seconds:.0f} s)")
    p(f"reports: {len(hid)}   mean rate {len(hid) / a.seconds:.1f}/s   link lost during run: {'YES at %.1f s' % lost[0] if lost else 'no'}")
    gaps, motion_gaps = [], []
    for i in range(1, len(hid)):
        dt = (hid[i][0] - hid[i - 1][0]) * 1000
        gaps.append(dt)
        # "during motion": the pad was sending changing reports both before and after this gap
        moving = i >= 2 and hid[i - 1][1] != hid[i - 2][1] and hid[i][1] != hid[i - 1][1]
        if moving:
            motion_gaps.append((hid[i - 1][0], dt))
    if gaps:
        p(f"all gaps (ms):     p50 {pct(gaps, 50):.1f}  p90 {pct(gaps, 90):.1f}  p99 {pct(gaps, 99):.1f}  max {max(gaps):.1f}")
    mg = [d for _, d in motion_gaps]
    if mg:
        p(f"gaps while moving: n {len(mg)}  p50 {pct(mg, 50):.1f}  p90 {pct(mg, 90):.1f}  p99 {pct(mg, 99):.1f}  max {max(mg):.1f}")
        p("gaps while moving over:  " + "   ".join(f">{b} ms: {sum(1 for d in mg if d > b)}" for b in GAP_BINS_MS))
        worst = sorted(motion_gaps, key=lambda x: -x[1])[:10]
        p("worst gaps while moving (t s, ms): " + ", ".join(f"{t:.1f}s {d:.0f}" for t, d in worst))
    changed = sum(1 for i in range(1, len(hid)) if hid[i][1] != hid[i - 1][1])
    p(f"reports that changed vs previous: {changed} ({100 * changed / max(1, len(hid) - 1):.0f}%)  "
      f"report sizes: {sorted({len(d) for _, d in hid})}")

    p("\n=== krbot (monitor feed, 10 Hz)")
    if status:
        hz = [m.get("loop_hz", 0) for _, m in status if m.get("loop_hz")]
        modes = [(t, m.get("mode")) for t, m in status]
        trans = [f"{t:.1f}s {a_}->{b_}" for (t, b_), (_, a_) in zip(modes[1:], modes[:-1]) if a_ != b_]
        first, last = status[0][1], status[-1][1]
        p(f"status msgs: {len(status)}   loop Hz min {min(hz):.1f} mean {statistics.mean(hz):.1f}" if hz else "no loop rate")
        p(f"mode changes: {', '.join(trans) or 'none'}")
        p(f"pad drops: {last.get('gamepad', {}).get('disconnects', 0) - first.get('gamepad', {}).get('disconnects', 0)}   "
          f"watchdog trips: {last.get('watchdog', {}).get('trips', 0) - first.get('watchdog', {}).get('trips', 0)}")
        live = [(t, m) for t, m in status if m.get("mode") == "ARMED" and m.get("inputs", {}).get("deadman")]
        p(f"time driving (ARMED + deadman held): ~{len(live) / 10:.1f} s")
        # held deadman but output collapsed to 0 while the stick was well off-centre = dropped command
        drop = [t for t, m in live if abs(m.get("inputs", {}).get("ly", 0)) > 0.3 and m.get("out", {}).get("left", 0) == 0
                and m.get("out", {}).get("right", 0) == 0]
        p(f"snapshots with stick >0.3 but zero output while driving: {len(drop)}" + (f" at {', '.join(f'{t:.1f}s' for t in drop[:10])}" if drop else ""))
        # a one-tick stop between snapshots shows as a sudden fall then a slew ramp: count falls of >50 %
        # from >=900 PWM while the stick barely moved (notes/bluetooth-debugging.md §8; expect 0)
        coll = [live[k][0] for k in range(1, len(live))
                if live[k][0] - live[k - 1][0] < 0.15
                and abs(live[k - 1][1].get("out", {}).get("left", 0)) >= 900
                and abs(live[k][1].get("out", {}).get("left", 0)) < 0.5 * abs(live[k - 1][1].get("out", {}).get("left", 0))
                and abs(live[k][1].get("inputs", {}).get("ly", 0) - live[k - 1][1].get("inputs", {}).get("ly", 0)) < 0.05]
        p(f"output collapses with the stick steady (>50 % fall from >=900): {len(coll)}"
          + (f" at {', '.join(f'{t:.1f}s' for t in coll[:10])}" if coll else ""))
    else:
        p("no status received - is krbot running?")
    warn = [f"{t:.1f}s {l}" for t, l in logs if " WARN " in l or " ERROR " in l or "->" in l]
    if warn:
        p("krbot events:\n  " + "\n  ".join(warn[:20]))

    p("\n=== Radio (1 Hz)")
    for key, label in (("bt_rssi", "BT RSSI (dB vs golden range, 0 = ideal)"), ("bt_lq", "BT link quality (0-255)"), ("wifi_dbm", "Wi-Fi level dBm")):
        v = [r[key] for r in radio if r[key] is not None]
        p(f"{label}: " + (f"min {min(v)} mean {statistics.mean(v):.1f} max {max(v)}" if v else "n/a"))

    text = "\n".join(out)
    print("\n" + text)
    path = a.out or f"/tmp/bt-probe-{time.strftime('%Y%m%d-%H%M%S')}.json"
    Path(path).write_text(json.dumps({
        "summary": text, "device": dev, "seconds": a.seconds,
        "hid": [[round(t, 4), d.hex()] for t, d in reports],
        "status": [[round(t, 3), m] for t, m in status], "logs": logs, "radio": radio}))
    os.chmod(path, 0o644)
    print(f"\nraw data: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
