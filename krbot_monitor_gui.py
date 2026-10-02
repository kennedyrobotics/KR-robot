#!/usr/bin/env python3
"""KR-bot Monitor — desktop client for the krbot C++ stack (client half of a client/server pair).

krbot (server) streams status at 10 Hz and every log event over TCP (line-delimited JSON,
krbot/src/core/MonitorServer.hpp). This window only displays it, plus two kinds of action:
  * E-STOP  -> {"cmd":"estop"} to krbot (SPACE / ESC / red button). There is no remote arm —
               arming stays on the gamepad (START), by design.
  * Service -> start / stop / dry-run / boot via `systemctl --user` (only when monitoring localhost).

    python3 krbot_monitor_gui.py                       # on the BeagleY-AI (desktop icon)
    python3 krbot_monitor_gui.py --host 192.168.1.116  # from the PC, if monitor.bind=0.0.0.0
    scripts\\monitor-from-pc.ps1                       # from the PC via an SSH tunnel (no firewall change)

Same look as KR-Robot Control (krc/ui_theme.py). Stdlib + tkinter only, so it runs on Windows too.
"""

from __future__ import annotations

import argparse
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, scrolledtext, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))

from krc.monitor_client import DEFAULT_PORT, MonitorClient  # noqa: E402
from krc.ui_theme import (ACCENT, BAD, BG, DIM, ENTRY_BG, ESTOP_RED, GOOD, MODE_COLOURS, MONO,  # noqa: E402
                          MUTED, PANEL, TEXT, UI, WARN, Bar, StickView, apply_style, button, card_row,
                          section)

APP_DIR = Path(__file__).resolve().parent
REFRESH_MS = 100
STALE_S = 1.5          # no status for this long while connected -> STALE
LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
UNIT = "krbot.service"


def fmt_uptime(s: float) -> str:
    s = int(s)
    return f"{s // 3600}h {s % 3600 // 60:02d}m {s % 60:02d}s" if s >= 3600 else f"{s // 60}m {s % 60:02d}s"


class MonitorApp(tk.Tk):
    def __init__(self, client: MonitorClient, local: bool) -> None:
        super().__init__()
        self.client = client
        self.local = local and shutil.which("systemctl") is not None
        self.status: dict = {}
        self.status_t = 0.0
        self.title(f"KR-bot Monitor — {client.host}:{client.port}")
        self.geometry("1180x800")
        self.minsize(980, 660)
        self.configure(bg=BG)
        icon = APP_DIR / "images" / "krbot_console.png"
        if icon.exists():
            try:
                self.iconphoto(True, tk.PhotoImage(file=str(icon)))
            except tk.TclError:
                pass
        apply_style(self)

        self._build_header()
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self._build_overview_tab()
        self._build_inputs_tab()
        self._build_events_tab()
        self._build_service_tab()

        for key in ("<space>", "<Escape>"):
            self.bind_all(key, lambda _e: self._estop())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._service_poll()
        self.after(REFRESH_MS, self._refresh)

    # ------------------------------------------------------------------ header
    def _build_header(self) -> None:
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill=tk.X, padx=6, pady=6)
        self._mode_lbl = tk.Label(bar, text="OFFLINE", font=(UI, 18, "bold"), bg=DIM, fg=BG,
                                  width=11, relief=tk.RAISED, bd=3, pady=6)
        self._mode_lbl.pack(side=tk.LEFT)
        mid = tk.Frame(bar, bg=BG)
        mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=12)
        self._reason_lbl = tk.Label(mid, text="", font=(UI, 11), bg=BG, fg=TEXT, anchor="w")
        self._reason_lbl.pack(fill=tk.X)
        self._links_lbl = tk.Label(mid, text="", font=(MONO, 10), bg=BG, fg=MUTED, anchor="w")
        self._links_lbl.pack(fill=tk.X)
        tk.Button(bar, text="E-STOP\n(space)", command=self._estop, bg=ESTOP_RED, fg="white",
                  activebackground="#ff4060", font=(UI, 16, "bold"), relief=tk.RAISED, bd=4,
                  width=10, takefocus=0).pack(side=tk.RIGHT)

    # ------------------------------------------------------------------ overview
    def _build_overview_tab(self) -> None:
        f = ttk.Frame(self._nb)
        self._nb.add(f, text="Overview")
        self._deadman_lbl = tk.Label(f, text="", font=(UI, 14, "bold"), bg=DIM, fg=BG, relief=tk.RAISED, bd=3, pady=8)
        self._deadman_lbl.pack(fill=tk.X, padx=12, pady=(10, 6))

        cards = tk.Frame(f, bg=BG)
        cards.pack(fill=tk.X, padx=6)
        srv = section(cards, "krbot server", side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._c_srv = {k: card_row(srv, k, width=11) for k in ("link", "endpoint", "version", "uptime", "motors mode")}
        pad = section(cards, "Gamepad (L5)", side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._c_pad = {k: card_row(pad, k, width=11) for k in ("status", "device", "drops")}
        mot = section(cards, "Motor board (L1)", side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._c_mot = {k: card_row(mot, k, width=11) for k in ("status", "driver", "watchdog")}

        out = section(f, "Track output (PWM)")
        self._out_bars = {}
        for side in ("Left (M1)", "Right (M2)"):
            r = tk.Frame(out, bg=PANEL)
            r.pack(fill=tk.X, pady=3)
            tk.Label(r, text=side, bg=PANEL, fg=MUTED, font=(UI, 10), width=12, anchor="w").pack(side=tk.LEFT)
            b = Bar(r, width=560, height=22)
            b.pack(side=tk.LEFT, padx=6)
            v = tk.Label(r, text="0", bg=PANEL, fg=TEXT, font=(MONO, 12, "bold"), width=7, anchor="e")
            v.pack(side=tk.LEFT)
            self._out_bars[side] = (b, v)

        layers = section(f, "5-layer stack (Design doc v3)")
        self._c_layer = {}
        for key, label in (("L5", "L5 Execution"), ("L4", "L4 Reasoning"), ("L3", "L3 Knowledge"),
                           ("L2", "L2 Perception"), ("L1", "L1 Driver")):
            self._c_layer[key] = card_row(layers, label, width=14)

        help_ = section(f, "Controls")
        for line in ("Gamepad: START arm (LB released, sticks centred) · hold LB deadman · B/HOME e-stop",
                     "This window: SPACE / ESC / E-STOP button -> latched E-STOP.  Re-arm only from the gamepad."):
            tk.Label(help_, text=line, bg=PANEL, fg=TEXT, font=(MONO, 9), anchor="w").pack(fill=tk.X)

    # ------------------------------------------------------------------ inputs
    def _build_inputs_tab(self) -> None:
        f = ttk.Frame(self._nb)
        self._nb.add(f, text="Inputs")
        top = tk.Frame(f, bg=BG)
        top.pack(fill=tk.X, padx=6, pady=6)
        self._sticks = []
        for label, kx, ky in (("Left stick (throttle)", "lx", "ly"), ("Right stick (steer)", "rx", "ry")):
            s = section(top, label, side=tk.LEFT, fill=tk.Y)
            v = StickView(s, size=170)
            v.pack()
            val = tk.Label(s, text="", bg=PANEL, fg=TEXT, font=(MONO, 9))
            val.pack()
            self._sticks.append((v, val, kx, ky))
        btns = section(top, "Teleop buttons (as krbot sees them)", side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._btn = {}
        for key, label in (("deadman", "LB  deadman"), ("arm", "START  arm"), ("estop", "B / HOME  e-stop"),
                           ("link", "gamepad link")):
            lbl = tk.Label(btns, text=label, width=26, bg=ENTRY_BG, fg=MUTED, font=(MONO, 11), pady=8,
                           relief=tk.GROOVE)
            lbl.pack(anchor="w", pady=3)
            self._btn[key] = lbl
        tk.Label(f, text="Shows the inputs krbot's control loop received on its last tick (10 Hz snapshot).",
                 bg=BG, fg=DIM, font=(UI, 9, "italic")).pack(anchor="w", padx=12)

    # ------------------------------------------------------------------ events
    def _build_events_tab(self) -> None:
        f = ttk.Frame(self._nb)
        self._nb.add(f, text="Events")
        r = tk.Frame(f, bg=BG)
        r.pack(fill=tk.X, padx=8, pady=6)
        button(r, "Clear", self._clear_log).pack(side=tk.LEFT, padx=2)
        button(r, "Save…", self._save_log).pack(side=tk.LEFT, padx=2)
        self._follow = tk.BooleanVar(value=True)
        tk.Checkbutton(r, text="follow", variable=self._follow, bg=BG, fg=TEXT, selectcolor=PANEL,
                       activebackground=BG, takefocus=0).pack(side=tk.LEFT, padx=8)
        # krbot logs a status summary every 2 s for the journal; it duplicates the live view here
        self._show_periodic = tk.BooleanVar(value=False)
        tk.Checkbutton(r, text="show periodic status lines", variable=self._show_periodic, bg=BG, fg=TEXT,
                       selectcolor=PANEL, activebackground=BG, takefocus=0).pack(side=tk.LEFT, padx=8)
        self._log = scrolledtext.ScrolledText(f, bg=ENTRY_BG, fg=TEXT, font=(MONO, 9), relief=tk.FLAT,
                                              state=tk.DISABLED, wrap=tk.NONE)
        self._log.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        for tag, col in (("WARN", WARN), ("ERROR", BAD), ("INFO", TEXT), ("DEBUG", DIM), ("client", ACCENT)):
            self._log.tag_configure(tag, foreground=col)

    # ------------------------------------------------------------------ service
    def _build_service_tab(self) -> None:
        f = ttk.Frame(self._nb)
        self._nb.add(f, text="Service")
        box = section(f, f"krbot.service (systemd --user){'' if self.local else ' — remote: control from the robot'}")
        self._c_svc = {k: card_row(box, k, width=12) for k in ("state", "boot", "args")}
        r = tk.Frame(box, bg=PANEL)
        r.pack(fill=tk.X, pady=(8, 2))
        st = tk.NORMAL if self.local else tk.DISABLED
        for text, cmd, col in (("Start", self._svc_start, GOOD), ("Start DRY RUN", self._svc_dry, ACCENT),
                               ("Stop", lambda: self._svc("stop"), WARN), ("Restart", lambda: self._svc("restart"), ACCENT),
                               ("Boot: on", lambda: self._svc("enable"), ACCENT),
                               ("Boot: off", lambda: self._svc("disable"), ACCENT),
                               ("Rebuild krbot", self._rebuild, ACCENT)):
            button(r, text, cmd, col, state=st).pack(side=tk.LEFT, padx=2)
        tk.Label(box, text="Stopping the service zeros the motors first. Any (re)start comes up DISARMED.\n"
                           "The motor port is exclusive: stop krbot before using the Python KR-Robot Control app.",
                 bg=PANEL, fg=DIM, font=(UI, 9, "italic"), justify=tk.LEFT, anchor="w").pack(fill=tk.X, pady=(6, 0))
        self._svc_out = scrolledtext.ScrolledText(f, height=12, bg=ENTRY_BG, fg=TEXT, font=(MONO, 9),
                                                  relief=tk.FLAT, state=tk.DISABLED)
        self._svc_out.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)

    # ------------------------------------------------------------------ actions
    def _estop(self) -> None:
        if self.client.send("estop"):
            self._append_log("client", "E-STOP sent to krbot")
        else:
            self._append_log("client", "E-STOP: not connected to krbot — if krbot is not running, motors are not driven")

    def _svc(self, *args: str) -> None:
        if not self.local:
            return

        def work():
            res = subprocess.run(["systemctl", "--user", *args, UNIT], capture_output=True, text=True)
            msg = f"$ systemctl --user {' '.join(args)} {UNIT}  -> {res.returncode}\n{res.stdout}{res.stderr}"
            self.after(0, lambda: (self._svc_append(msg), self._service_poll(once=True)))

        threading.Thread(target=work, daemon=True).start()

    def _svc_start(self) -> None:
        subprocess.run(["systemctl", "--user", "unset-environment", "KRBOT_ARGS"])
        self._svc("restart")

    def _svc_dry(self) -> None:
        subprocess.run(["systemctl", "--user", "set-environment", "KRBOT_ARGS=--dry-run"])
        self._svc("restart")

    def _rebuild(self) -> None:
        self._svc_append("$ scripts/build-krbot.sh (NO_TESTS=1) …\n")

        def work():
            res = subprocess.run(["bash", str(APP_DIR / "scripts" / "build-krbot.sh")], capture_output=True,
                                 text=True, env={**os.environ, "NO_TESTS": "1"})
            tail = "\n".join((res.stdout + res.stderr).splitlines()[-15:])
            msg = f"{tail}\n-> exit {res.returncode}{'  (press Restart to run the new binary)' if res.returncode == 0 else ''}\n"
            self.after(0, lambda: self._svc_append(msg))

        threading.Thread(target=work, daemon=True).start()

    def _svc_append(self, text: str) -> None:
        self._svc_out.configure(state=tk.NORMAL)
        self._svc_out.insert(tk.END, text if text.endswith("\n") else text + "\n")
        self._svc_out.see(tk.END)
        self._svc_out.configure(state=tk.DISABLED)

    def _service_poll(self, once: bool = False) -> None:
        if self.local:
            def q(*a):
                r = subprocess.run(["systemctl", "--user", *a], capture_output=True, text=True)
                return r.stdout.strip()
            active, enabled = q("is-active", UNIT) or "?", q("is-enabled", UNIT) or "?"
            env = q("show-environment")
            args = next((l.split("=", 1)[1] for l in env.splitlines() if l.startswith("KRBOT_ARGS=")), "")
            self._c_svc["state"].configure(text=active, fg=GOOD if active == "active" else BAD)
            self._c_svc["boot"].configure(text=enabled)
            self._c_svc["args"].configure(text=args or "(real motor board)", fg=WARN if args else TEXT)
        else:
            self._c_svc["state"].configure(text="remote host — see server card")
        if not once:
            self.after(2000, self._service_poll)

    def _clear_log(self) -> None:
        self._log.configure(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.configure(state=tk.DISABLED)

    def _save_log(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".log", initialfile="krbot-monitor.log")
        if path:
            Path(path).write_text(self._log.get("1.0", tk.END))

    def _append_log(self, tag: str, line: str) -> None:
        self._log.configure(state=tk.NORMAL)
        if tag == "client":
            line = f"{time.strftime('%H:%M:%S')}  [monitor] {line}"
        self._log.insert(tk.END, line + "\n", tag)
        if int(self._log.index("end-1c").split(".")[0]) > 5000:
            self._log.delete("1.0", "1000.0")
        if self._follow.get():
            self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        self.client.stop()       # closing the monitor never stops the robot
        self.destroy()

    # ------------------------------------------------------------------ refresh
    def _refresh(self) -> None:
        try:
            while True:
                msg = self.client.messages.get_nowait()
                t = msg.get("type")
                if t == "status":
                    self.status, self.status_t = msg, time.monotonic()
                elif t == "log":
                    line = msg.get("line", "")
                    if self._show_periodic.get() or not ("[core]" in line and "| pad " in line):
                        self._append_log(msg.get("level", "INFO"), line)
                elif t == "_connected":
                    self._append_log("client", f"connected to krbot at {msg['host']}:{msg['port']}")
                elif t == "_disconnected":
                    self.status = {}
                    self._append_log("client", f"disconnected ({msg.get('error') or 'closed'}) — retrying")
                elif t == "ack":
                    self._append_log("client", f"krbot acknowledged {msg.get('cmd')}")
                elif t == "hello":
                    self._append_log("client", f"krbot {msg.get('version')} (protocol {msg.get('protocol')})")
        except queue.Empty:
            pass
        try:
            self._update()
        finally:
            self.after(REFRESH_MS, self._refresh)

    def _update(self) -> None:
        c, s = self.client, self.status
        stale = c.connected and s and time.monotonic() - self.status_t > STALE_S
        if not c.connected or not s:
            mode, colour = ("OFFLINE", DIM) if not c.connected else ("WAITING", DIM)
            reason = (f"krbot not reachable — {c.last_error or 'connecting…'}   (is the service running?)"
                      if not c.connected else "connected, waiting for first status")
        elif stale:
            mode, colour, reason = "STALE", WARN, f"no status for {time.monotonic() - self.status_t:.1f} s"
        else:
            mode = s.get("mode", "?")
            colour = MODE_COLOURS.get(mode, WARN)
            reason = s.get("mode_reason", "") + ("   [DRY RUN — simulated motors]" if s.get("dry_run") else "")
        self._mode_lbl.configure(text=mode, bg=colour)
        self._reason_lbl.configure(text=reason)

        live = bool(c.connected and s and not stale)
        pad, mot, inputs = s.get("gamepad", {}), s.get("motors", {}), s.get("inputs", {})
        self._links_lbl.configure(text=(
            f"server: {'OK' if c.connected else 'DOWN'} {c.host}:{c.port}    "
            f"gamepad: {'OK' if pad.get('connected') else 'none'}    "
            f"motors: {'OK' if mot.get('healthy') else 'FAULT' if s else '-'}    "
            f"loop: {s.get('loop_hz', 0):.0f} Hz" if live else f"server: DOWN {c.host}:{c.port}"))

        if live and mode == "ARMED" and inputs.get("deadman"):
            self._deadman_lbl.configure(text="●  DEADMAN HELD — ROBOT LIVE", bg=GOOD)
        elif live and mode == "ARMED":
            self._deadman_lbl.configure(text="○  ARMED — hold LB to drive", bg=WARN)
        elif live:
            self._deadman_lbl.configure(text=f"○  {mode} — press START on the gamepad to arm", bg=BAD)
        else:
            self._deadman_lbl.configure(text="○  no live data from krbot", bg=DIM)

        # cards
        self._c_srv["link"].configure(text="connected" if c.connected else "disconnected",
                                      fg=GOOD if c.connected else BAD)
        self._c_srv["endpoint"].configure(text=f"{c.host}:{c.port}  (clients {s.get('monitor_clients', '-')})")
        self._c_srv["version"].configure(text=s.get("version", "—"))
        self._c_srv["uptime"].configure(text=fmt_uptime(s["uptime_s"]) if "uptime_s" in s else "—")
        self._c_srv["motors mode"].configure(text=("DRY RUN" if s.get("dry_run") else "real board") if s else "—",
                                             fg=WARN if s.get("dry_run") else TEXT)
        self._c_pad["status"].configure(text=("connected" if pad.get("connected") else "waiting…") if s else "—",
                                        fg=GOOD if pad.get("connected") else BAD)
        self._c_pad["device"].configure(text=(pad.get("name") or "—")[:30])
        self._c_pad["drops"].configure(text=str(pad.get("disconnects", "—")))
        self._c_mot["status"].configure(text=("healthy" if mot.get("healthy") else "FAULT") if s else "—",
                                        fg=GOOD if mot.get("healthy") else BAD)
        self._c_mot["driver"].configure(text=(mot.get("desc") or "—")[:34])
        wd = s.get("watchdog", {})
        self._c_mot["watchdog"].configure(text=f"{wd.get('trips', '-')} trips / {wd.get('timeout_ms', '-')} ms",
                                          fg=BAD if wd.get("trips") else TEXT)

        out = s.get("out", {})
        scale = max(1, s.get("max_output", 1800))
        for (bar, lbl), v in zip(self._out_bars.values(), (out.get("left", 0), out.get("right", 0))):
            bar.set(v / scale, GOOD if v else DIM)
            lbl.configure(text=f"{v:+d}")

        g, rs = s.get("goal", {}), s.get("reasoner", {})
        goal = f"{g.get('type')}({g.get('target')}) prio {g.get('priority')} {g.get('task')}" if g.get("type") else "none"
        self._c_layer["L5"].configure(text=f"{mode:<9} loop {s.get('loop_hz', 0):.0f} Hz | goal {goal} | pending {g.get('pending', 0)}" if s else "—")
        self._c_layer["L4"].configure(text=f"NullReasoner (CLIPS in Phase 1) | ticks {rs.get('ticks', 0)} | deltas {rs.get('deltas', 0)} | saturated {rs.get('saturated', 0)}" if s else "—")
        self._c_layer["L3"].configure(text=f"FactStore: {s.get('facts', 0)} facts" if s else "—")
        self._c_layer["L2"].configure(text="PerceptionLoop: motor-link health source" if s else "—")
        self._c_layer["L1"].configure(text=f"{mot.get('desc', '—')} | {'healthy' if mot.get('healthy') else 'FAULT'}" if s else "—")

        # inputs tab
        for view, val, kx, ky in self._sticks:
            x, y = inputs.get(kx, 0.0), inputs.get(ky, 0.0)
            view.set(x, y, abs(x) > 0.08 or abs(y) > 0.08)
            val.configure(text=f"x {x:+.2f}  y {y:+.2f}")
        for key, lbl in self._btn.items():
            on = bool(inputs.get(key)) if live else False
            good = key in ("link",)
            lbl.configure(bg=(GOOD if good else (BAD if key == "estop" else ACCENT)) if on else ENTRY_BG,
                          fg=BG if on else MUTED)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    a = ap.parse_args()
    if sys.platform.startswith("linux") and not os.environ.get("DISPLAY"):
        os.environ["DISPLAY"] = ":0"
    client = MonitorClient(a.host, a.port)
    client.start()
    try:
        app = MonitorApp(client, local=a.host in LOCAL_HOSTS)
    except tk.TclError as e:
        print(f"Cannot open display: {e}")
        return 2
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
