#!/usr/bin/env python3
"""KR-Robot Control — desktop app for the BeagleY-AI (tkinter).

Front-end over krc.core.RobotCore, which runs the 50 Hz teleop loop (SN2403 gamepad ->
Yahboom motor board) on a background thread. This GUI polls `core.snapshot()` on a timer
and sends actions through RobotCore's thread-safe methods — it never touches the serial
port or the gamepad directly. Layout and palette follow the atr-viu-emulator dashboard.

Tabs: Drive (status + E-STOP) | Joystick | Motor board | Log | Diagnostics

    DISPLAY=:0 python3 ~/krc-robot/krc_robot_gui.py [--dry-run] [--dev /dev/input/eventN]

SPACE or ESC anywhere = E-STOP. Re-arming is only possible from the gamepad (START).
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent))

from krc import joystick as js  # noqa: E402
from krc.core import PROFILES, RobotCore, Settings  # noqa: E402
from krc.ui_theme import (BAD, BG, DIM, ENTRY_BG, GOOD, MODE_COLOURS, MONO, MUTED,  # noqa: E402
                          PANEL, TEXT, UI, WARN, Bar, StickView, apply_style, button, card_row, section)

APP_DIR = Path(__file__).resolve().parent
REFRESH_MS = 100

STICKS = [("Left stick", js.ABS_X, js.ABS_Y), ("Right stick", js.ABS_RX, js.ABS_RY)]
TRIGGERS = [("LT", js.ABS_Z), ("RT", js.ABS_RZ)]
BUTTON_ORDER = [js.BTN_SOUTH, js.BTN_EAST, js.BTN_WEST, js.BTN_NORTH, js.BTN_TL, js.BTN_TR,
                js.BTN_TL2, js.BTN_TR2, js.BTN_SELECT, js.BTN_START, js.BTN_MODE, js.BTN_THUMBL, js.BTN_THUMBR]
BUTTON_ROLES = {js.BTN_TL: "deadman", js.BTN_START: "arm", js.BTN_EAST: "e-stop", js.BTN_MODE: "e-stop"}

class KrRobotApp(tk.Tk):
    def __init__(self, core: RobotCore) -> None:
        super().__init__()
        self.core = core
        self.title("KR-Robot Control")
        self.geometry("1180x780")
        self.minsize(980, 640)
        self.configure(bg=BG)
        icon = APP_DIR / "images" / "app_icon.png"
        if icon.exists():
            try:
                self.iconphoto(True, tk.PhotoImage(file=str(icon)))
            except tk.TclError:
                pass

        apply_style(self)

        self._log_seen = 0
        self._build_header()
        self._nb = ttk.Notebook(self)
        self._nb.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))
        self._build_drive_tab()
        self._build_joystick_tab()
        self._build_motor_tab()
        self._build_log_tab()
        self._build_diag_tab()

        for key in ("<space>", "<Escape>"):
            self.bind_all(key, lambda _e: self._estop())
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(REFRESH_MS, self._refresh)

    # ------------------------------------------------------------------ header
    def _build_header(self) -> None:
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill=tk.X, padx=6, pady=6)
        self._mode_lbl = tk.Label(bar, text="DISARMED", font=(UI, 18, "bold"), bg=WARN, fg=BG,
                                  width=11, relief=tk.RAISED, bd=3, pady=6)
        self._mode_lbl.pack(side=tk.LEFT)
        mid = tk.Frame(bar, bg=BG)
        mid.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=12)
        self._reason_lbl = tk.Label(mid, text="", font=(UI, 11), bg=BG, fg=TEXT, anchor="w")
        self._reason_lbl.pack(fill=tk.X)
        self._links_lbl = tk.Label(mid, text="", font=(MONO, 10), bg=BG, fg=MUTED, anchor="w")
        self._links_lbl.pack(fill=tk.X)
        tk.Button(bar, text="E-STOP\n(space)", command=self._estop, bg="#d20f39", fg="white",
                  activebackground="#ff4060", font=(UI, 16, "bold"), relief=tk.RAISED, bd=4,
                  width=10).pack(side=tk.RIGHT)

    # ------------------------------------------------------------------ drive
    def _build_drive_tab(self) -> None:
        f = ttk.Frame(self._nb)
        self._nb.add(f, text="Drive")

        self._deadman_lbl = tk.Label(f, text="", font=(UI, 14, "bold"), bg=BAD, fg=BG,
                                     relief=tk.RAISED, bd=3, pady=8)
        self._deadman_lbl.pack(fill=tk.X, padx=12, pady=(10, 6))

        cards = tk.Frame(f, bg=BG)
        cards.pack(fill=tk.X, padx=6)
        pad = section(cards, "Gamepad (L5)", side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._c_pad = {k: card_row(pad, k) for k in ("status", "device", "bus / id", "rumble", "drops")}
        mot = section(cards, "Motor board (L1)", side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._c_mot = {k: card_row(mot, k) for k in ("status", "port", "profile", "last RX", "error")}
        row = tk.Frame(mot, bg=PANEL)
        row.pack(fill=tk.X, pady=(6, 0))
        button(row, "Connect", self.core.connect_motors, GOOD).pack(side=tk.LEFT, padx=2)
        button(row, "Disconnect", self.core.disconnect_motors, WARN).pack(side=tk.LEFT, padx=2)
        loop = section(cards, "Control loop", side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._c_loop = {k: card_row(loop, k) for k in ("rate", "max PWM", "mixing", "invert", "output")}

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

        help_ = section(f, "Controls")
        for line in ("START  arm  (LB released, sticks centred)        hold LB  deadman — moves only while held",
                     "Left stick Y  throttle    Right stick X  steer   (tank mode: left Y / right Y)",
                     "B or HOME  e-stop (latched)    GUI: SPACE / ESC / E-STOP button — re-arm only from the pad"):
            tk.Label(help_, text=line, bg=PANEL, fg=TEXT, font=(MONO, 9), anchor="w").pack(fill=tk.X)

    # ------------------------------------------------------------------ joystick
    def _build_joystick_tab(self) -> None:
        f = ttk.Frame(self._nb)
        self._nb.add(f, text="Joystick")
        top = tk.Frame(f, bg=BG)
        top.pack(fill=tk.X, padx=6, pady=6)

        self._stick_views = []
        for label, ax, ay in STICKS:
            s = section(top, label, side=tk.LEFT, fill=tk.Y)
            v = StickView(s)
            v.pack()
            val = tk.Label(s, text="", bg=PANEL, fg=TEXT, font=(MONO, 9))
            val.pack()
            self._stick_views.append((v, val, ax, ay))

        trg = section(top, "Triggers / D-pad", side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._trig_bars = {}
        for name, code in TRIGGERS:
            r = tk.Frame(trg, bg=PANEL)
            r.pack(fill=tk.X, pady=3)
            tk.Label(r, text=name, bg=PANEL, fg=MUTED, font=(UI, 10), width=4).pack(side=tk.LEFT)
            b = Bar(r, width=200, one_sided=True)
            b.pack(side=tk.LEFT)
            self._trig_bars[code] = b
        self._dpad = tk.Label(trg, text="", bg=PANEL, fg=TEXT, font=(MONO, 14, "bold"), justify=tk.CENTER)
        self._dpad.pack(pady=6)
        rr = tk.Frame(trg, bg=PANEL)
        rr.pack(fill=tk.X)
        button(rr, "Rumble strong", lambda: self._rumble(1.0, 0.0)).pack(side=tk.LEFT, padx=2)
        button(rr, "Rumble weak", lambda: self._rumble(0.0, 1.0)).pack(side=tk.LEFT, padx=2)

        btns = section(f, "Buttons — live state / presses (verify every button at least once)")
        grid = tk.Frame(btns, bg=PANEL)
        grid.pack(fill=tk.X)
        self._btn_lbls = {}
        for i, code in enumerate(BUTTON_ORDER):
            lbl = tk.Label(grid, text="", width=17, bg=ENTRY_BG, fg=MUTED, font=(MONO, 10), pady=6, relief=tk.GROOVE)
            lbl.grid(row=i // 7, column=i % 7, padx=3, pady=3)
            self._btn_lbls[code] = lbl
        self._verified_lbl = tk.Label(btns, text="", bg=PANEL, fg=WARN, font=(UI, 10, "bold"), anchor="w")
        self._verified_lbl.pack(fill=tk.X, pady=(6, 0))

        axes = section(f, "All axes (normalised)")
        self._axis_rows = tk.Frame(axes, bg=PANEL)
        self._axis_rows.pack(fill=tk.X)
        self._axis_widgets: dict[int, tuple[Bar, tk.Label]] = {}

    # ------------------------------------------------------------------ motor board
    def _build_motor_tab(self) -> None:
        f = ttk.Frame(self._nb)
        self._nb.add(f, text="Motor board")
        s = self.core.settings
        cols = tk.Frame(f, bg=BG)
        cols.pack(fill=tk.BOTH, expand=True)
        left = tk.Frame(cols, bg=BG)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        right = tk.Frame(cols, bg=BG)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        cfg = section(left, "Settings (saved to ~/.config/krc-robot/settings.json)")
        self._v = {
            "port": tk.StringVar(value=s.port), "pwm_cmd": tk.StringVar(value=s.pwm_cmd),
            "profile": tk.StringVar(value=s.profile), "init": tk.BooleanVar(value=s.init_on_connect),
            "max_pwm": tk.IntVar(value=s.teleop.max_pwm), "turn": tk.DoubleVar(value=s.teleop.turn_scale),
            "slew": tk.DoubleVar(value=s.teleop.slew_per_s), "dead": tk.DoubleVar(value=s.teleop.stick_deadband),
            "expo": tk.DoubleVar(value=s.teleop.expo), "tank": tk.BooleanVar(value=s.teleop.tank),
            "inv_l": tk.BooleanVar(value=s.teleop.invert_left), "inv_r": tk.BooleanVar(value=s.teleop.invert_right),
        }

        def entry(label, var, width=18):
            r = tk.Frame(cfg, bg=PANEL)
            r.pack(fill=tk.X, pady=1)
            tk.Label(r, text=label, bg=PANEL, fg=MUTED, font=(UI, 9), width=22, anchor="w").pack(side=tk.LEFT)
            tk.Entry(r, textvariable=var, bg=ENTRY_BG, fg=TEXT, insertbackground=TEXT, font=(MONO, 10),
                     width=width, relief=tk.FLAT).pack(side=tk.LEFT, padx=4)

        def scale(label, var, lo, hi, res):
            r = tk.Frame(cfg, bg=PANEL)
            r.pack(fill=tk.X, pady=1)
            tk.Label(r, text=label, bg=PANEL, fg=MUTED, font=(UI, 9), width=22, anchor="w").pack(side=tk.LEFT)
            tk.Scale(r, variable=var, from_=lo, to=hi, resolution=res, orient=tk.HORIZONTAL, length=240,
                     bg=PANEL, fg=TEXT, troughcolor=ENTRY_BG, highlightthickness=0).pack(side=tk.LEFT)

        def check(label, var):
            tk.Checkbutton(cfg, text=label, variable=var, bg=PANEL, fg=TEXT, selectcolor=BG,
                           activebackground=PANEL, font=(UI, 9), anchor="w").pack(fill=tk.X)

        entry("Serial port", self._v["port"])
        entry("PWM command keyword", self._v["pwm_cmd"])
        r = tk.Frame(cfg, bg=PANEL)
        r.pack(fill=tk.X, pady=1)
        tk.Label(r, text="Motor profile", bg=PANEL, fg=MUTED, font=(UI, 9), width=22, anchor="w").pack(side=tk.LEFT)
        ttk.Combobox(r, textvariable=self._v["profile"], values=list(PROFILES), state="readonly",
                     width=16).pack(side=tk.LEFT, padx=4)
        check("Send profile init on connect", self._v["init"])
        scale("Max PWM (bench cap)", self._v["max_pwm"], 0, 3600, 50)
        scale("Turn scale", self._v["turn"], 0.1, 1.0, 0.05)
        scale("Slew (/s)", self._v["slew"], 0.5, 10.0, 0.5)
        scale("Stick deadband", self._v["dead"], 0.0, 0.3, 0.01)
        scale("Expo", self._v["expo"], 0.0, 1.0, 0.05)
        check("Tank mode (left Y / right Y)", self._v["tank"])
        check("Invert left track", self._v["inv_l"])
        check("Invert right track", self._v["inv_r"])
        r = tk.Frame(cfg, bg=PANEL)
        r.pack(fill=tk.X, pady=(6, 0))
        button(r, "Apply + save", self._apply_settings, GOOD).pack(side=tk.LEFT, padx=2)
        button(r, "Send profile init", self.core.init_profile).pack(side=tk.LEFT, padx=2)
        tk.Label(cfg, text="Port / keyword changes take effect on the next Connect.", bg=PANEL, fg=DIM,
                 font=(UI, 8, "italic"), anchor="w").pack(fill=tk.X)

        bench = section(right, "Bench test — one channel  (TRACKS OFF THE GROUND)")
        self._bench_ch = tk.IntVar(value=1)
        self._bench_pwm = tk.IntVar(value=600)
        self._bench_dur = tk.DoubleVar(value=1.0)
        self._bench_ok = tk.BooleanVar(value=False)
        r = tk.Frame(bench, bg=PANEL)
        r.pack(fill=tk.X)
        tk.Label(r, text="Channel", bg=PANEL, fg=MUTED, font=(UI, 9)).pack(side=tk.LEFT)
        for ch in (1, 2, 3, 4):
            tk.Radiobutton(r, text=f"M{ch}", variable=self._bench_ch, value=ch, bg=PANEL, fg=TEXT,
                           selectcolor=BG, activebackground=PANEL).pack(side=tk.LEFT)
        for label, var, lo, hi, res in (("PWM", self._bench_pwm, -1800, 1800, 50),
                                        ("Seconds", self._bench_dur, 0.2, 5.0, 0.1)):
            rr = tk.Frame(bench, bg=PANEL)
            rr.pack(fill=tk.X)
            tk.Label(rr, text=label, bg=PANEL, fg=MUTED, font=(UI, 9), width=8, anchor="w").pack(side=tk.LEFT)
            tk.Scale(rr, variable=var, from_=lo, to=hi, resolution=res, orient=tk.HORIZONTAL, length=300,
                     bg=PANEL, fg=TEXT, troughcolor=ENTRY_BG, highlightthickness=0).pack(side=tk.LEFT)
        tk.Checkbutton(bench, text="I confirm the tracks are off the ground", variable=self._bench_ok,
                       bg=PANEL, fg=WARN, selectcolor=BG, activebackground=PANEL, font=(UI, 9, "bold")).pack(anchor="w")
        button(bench, "Pulse", self._bench_pulse, WARN).pack(anchor="w", pady=4)

        proto = section(right, "Protocol / telemetry")
        self._up = [tk.BooleanVar(value=False) for _ in range(3)]
        r = tk.Frame(proto, bg=PANEL)
        r.pack(fill=tk.X)
        for var, name in zip(self._up, ("$MAll total", "$MTEP 10 ms", "$MSPD speed")):
            tk.Checkbutton(r, text=name, variable=var, bg=PANEL, fg=TEXT, selectcolor=BG,
                           activebackground=PANEL, font=(UI, 9)).pack(side=tk.LEFT)
        button(r, "Set $upload", lambda: self.core.set_upload(*(v.get() for v in self._up))).pack(side=tk.LEFT, padx=6)
        r = tk.Frame(proto, bg=PANEL)
        r.pack(fill=tk.X, pady=4)
        self._raw = tk.StringVar(value="$upload:1,0,0#")
        tk.Entry(r, textvariable=self._raw, bg=ENTRY_BG, fg=TEXT, insertbackground=TEXT, font=(MONO, 10),
                 width=30, relief=tk.FLAT).pack(side=tk.LEFT)
        button(r, "Send raw", self._send_raw).pack(side=tk.LEFT, padx=4)
        self._telem = {k: card_row(proto, k, width=10) for k in ("total", "per 10 ms", "mm/s", "age")}
        tk.Label(proto, text="Unrecognised frames from the board:", bg=PANEL, fg=MUTED, font=(UI, 9),
                 anchor="w").pack(fill=tk.X, pady=(4, 0))
        self._other = tk.Text(proto, height=7, bg=ENTRY_BG, fg=TEXT, font=(MONO, 9), relief=tk.FLAT)
        self._other.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------ log
    def _build_log_tab(self) -> None:
        f = ttk.Frame(self._nb)
        self._nb.add(f, text="Log")
        r = tk.Frame(f, bg=BG)
        r.pack(fill=tk.X, padx=8, pady=6)
        button(r, "Clear", lambda: self._set_text(self._log, "")).pack(side=tk.LEFT, padx=2)
        button(r, "Save…", self._save_log).pack(side=tk.LEFT, padx=2)
        self._log = scrolledtext.ScrolledText(f, bg=ENTRY_BG, fg=TEXT, font=(MONO, 9), relief=tk.FLAT,
                                              insertbackground=TEXT, state=tk.DISABLED, wrap=tk.WORD)
        self._log.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        for tag, col in (("warn", WARN), ("error", BAD), ("info", TEXT)):
            self._log.tag_configure(tag, foreground=col)

    # ------------------------------------------------------------------ diagnostics
    def _build_diag_tab(self) -> None:
        f = ttk.Frame(self._nb)
        self._nb.add(f, text="Diagnostics")
        r = tk.Frame(f, bg=BG)
        r.pack(fill=tk.X, padx=8, pady=6)
        button(r, "Run krc-diag.sh", self._run_diag, GOOD).pack(side=tk.LEFT)
        tk.Label(r, text="  USB serial, gamepad, Bluetooth, groups and udev checks (no sudo)",
                 bg=BG, fg=DIM, font=(UI, 9, "italic")).pack(side=tk.LEFT)
        self._diag = scrolledtext.ScrolledText(f, bg=ENTRY_BG, fg=TEXT, font=(MONO, 9), relief=tk.FLAT,
                                               state=tk.DISABLED)
        self._diag.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

    # ------------------------------------------------------------------ actions
    def _estop(self) -> None:
        self.core.estop("GUI e-stop")

    def _rumble(self, strong: float, weak: float) -> None:
        err = self.core.rumble(strong, weak, 400)
        if err:
            messagebox.showwarning("Rumble", err)

    def _apply_settings(self) -> None:
        s, v = self.core.settings, self._v
        try:
            s.port, s.pwm_cmd, s.profile = v["port"].get().strip(), v["pwm_cmd"].get().strip(), v["profile"].get()
            s.init_on_connect = v["init"].get()
            t = s.teleop
            t.max_pwm, t.turn_scale, t.slew_per_s = int(v["max_pwm"].get()), v["turn"].get(), v["slew"].get()
            t.stick_deadband, t.expo, t.tank = v["dead"].get(), v["expo"].get(), v["tank"].get()
            t.invert_left, t.invert_right = v["inv_l"].get(), v["inv_r"].get()
        except (tk.TclError, ValueError) as e:
            messagebox.showerror("Settings", f"Invalid value: {e}")
            return
        self.core.apply_settings()
        self.core.note("settings applied and saved")

    def _bench_pulse(self) -> None:
        if not self._bench_ok.get():
            messagebox.showwarning("Bench test", "Lift the chassis so the tracks are off the ground, then tick the box.")
            return
        err = self.core.bench_pulse(self._bench_ch.get(), self._bench_pwm.get(), self._bench_dur.get())
        if err:
            messagebox.showwarning("Bench test", err)

    def _send_raw(self) -> None:
        frame = self._raw.get().strip()
        if not (frame.startswith("$") and frame.endswith("#")):
            messagebox.showerror("Raw frame", "Frames look like  $cmd:args#")
            return
        self.core.send_raw(frame)

    def _save_log(self) -> None:
        path = filedialog.asksaveasfilename(defaultextension=".log", initialfile="krc-robot.log")
        if path:
            Path(path).write_text(self._log.get("1.0", tk.END))

    def _run_diag(self) -> None:
        self._set_text(self._diag, "running…\n")

        def work():
            try:
                out = subprocess.run(["bash", str(APP_DIR / "scripts" / "krc-diag.sh")], capture_output=True,
                                     text=True, timeout=30).stdout
            except (OSError, subprocess.TimeoutExpired) as e:
                out = f"failed: {e}"
            out = re.sub(r"\x1b\[[0-9;]*m", "", out)
            self.after(0, lambda: self._set_text(self._diag, out))

        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _set_text(widget, text: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)
        widget.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        self.core.shutdown()      # zero PWM, close port and gamepad
        self.destroy()

    # ------------------------------------------------------------------ refresh
    def _refresh(self) -> None:
        try:
            self._update(self.core.snapshot())
        finally:
            self.after(REFRESH_MS, self._refresh)

    def _update(self, sn: dict) -> None:
        mode = sn["mode"]
        self._mode_lbl.configure(text=mode, bg=MODE_COLOURS.get(mode, WARN))
        self._reason_lbl.configure(text=f"{sn['reason']}{'   [bench pulse active]' if sn['bench'] else ''}"
                                        f"{'   [DRY RUN — no motor output]' if sn['dry_run'] else ''}")
        pad, mot = sn["pad"], sn["motors"]
        self._links_lbl.configure(
            text=f"gamepad: {'OK ' + pad['bus'] if pad else 'NOT CONNECTED'}    "
                 f"motor board: {'OK ' + mot['port'] if mot else 'NOT CONNECTED'}    loop: {sn['loop_hz']:.0f} Hz")

        held = bool(pad and pad["buttons"].get(js.BTN_TL))
        if mode == "ARMED" and held:
            self._deadman_lbl.configure(text="●  DEADMAN HELD — ROBOT LIVE", bg=GOOD)
        elif mode == "ARMED":
            self._deadman_lbl.configure(text="○  ARMED — hold LB to drive", bg=WARN)
        else:
            self._deadman_lbl.configure(text=f"○  {mode} — press START on the pad to arm", bg=BAD)

        # drive tab cards
        if pad:
            self._c_pad["status"].configure(text="connected", fg=GOOD)
            self._c_pad["device"].configure(text=pad["name"][:34])
            self._c_pad["bus / id"].configure(text=f"{pad['bus']}  {pad['id']}")
            self._c_pad["rumble"].configure(text="yes" if pad["rumble"] else "no")
        else:
            self._c_pad["status"].configure(text="waiting…", fg=BAD)
            for k in ("device", "bus / id", "rumble"):
                self._c_pad[k].configure(text="—")
        self._c_pad["drops"].configure(text=f"{sn['pad_disconnects']} (connects {sn['pad_connects']})")

        s = self.core.settings
        if mot:
            age = mot["telemetry"]["age"]
            self._c_mot["status"].configure(text="connected", fg=GOOD)
            self._c_mot["port"].configure(text=mot["port"])
            self._c_mot["last RX"].configure(text="none yet" if age is None else f"{age:.1f} s ago")
        else:
            self._c_mot["status"].configure(text="dry run" if sn["dry_run"] else "not connected",
                                            fg=WARN if sn["dry_run"] else BAD)
            self._c_mot["port"].configure(text=s.port)
            self._c_mot["last RX"].configure(text="—")
        self._c_mot["profile"].configure(text=f"{s.profile}  ${s.pwm_cmd}:")
        self._c_mot["error"].configure(text=(sn["motor_error"] or "—")[:40], fg=BAD if sn["motor_error"] else TEXT)

        t = s.teleop
        self._c_loop["rate"].configure(text=f"{sn['loop_hz']:.0f} Hz")
        self._c_loop["max PWM"].configure(text=f"{t.max_pwm} / 3600")
        self._c_loop["mixing"].configure(text="tank" if t.tank else f"arcade (turn {t.turn_scale:.2f})")
        self._c_loop["invert"].configure(text=f"L={t.invert_left} R={t.invert_right}")
        l, r = sn["out"]
        self._c_loop["output"].configure(text=f"L {l:+5d}  R {r:+5d}")
        scale = max(1, sn["max_pwm"])
        for (bar, lbl), v in zip(self._out_bars.values(), (l, r)):
            bar.set(v / scale, GOOD if v else DIM)
            lbl.configure(text=f"{v:+d}")

        self._update_joystick(pad, sn["presses"])
        self._update_motor_tab(mot)
        self._update_log()

    def _update_joystick(self, pad, presses) -> None:
        axes = pad["axes"] if pad else {}
        buttons = pad["buttons"] if pad else {}
        for view, val, ax, ay in self._stick_views:
            x, y = axes.get(ax, 0.0), axes.get(ay, 0.0)
            view.set(x, y, abs(x) > 0.08 or abs(y) > 0.08)
            val.configure(text=f"x {x:+.2f}  y {y:+.2f}")
        for code, bar in self._trig_bars.items():
            bar.set(axes.get(code, 0.0))
        hx, hy = axes.get(js.ABS_HAT0X, 0.0), axes.get(js.ABS_HAT0Y, 0.0)
        self._dpad.configure(text=f"  {'▲' if hy < -0.5 else '△'}\n{'◀' if hx < -0.5 else '◁'}   {'▶' if hx > 0.5 else '▷'}"
                                  f"\n  {'▼' if hy > 0.5 else '▽'}")
        present = [c for c in BUTTON_ORDER if c in buttons]
        for code, lbl in self._btn_lbls.items():
            name = js.BTN_NAMES.get(code, hex(code))
            role = BUTTON_ROLES.get(code, "")
            n = presses.get(code, 0)
            if code not in buttons:
                lbl.configure(text=f"{name} (n/a)", bg=ENTRY_BG, fg=DIM)
            elif buttons[code]:
                lbl.configure(text=f"{name} {role}".strip(), bg=GOOD, fg=BG)
            else:
                lbl.configure(text=f"{name} ×{n} {role}".strip(), bg=ENTRY_BG, fg=GOOD if n else MUTED)
        done = sum(1 for c in present if presses.get(c, 0))
        self._verified_lbl.configure(
            text=f"Verified {done}/{len(present)} buttons" if pad else "No gamepad connected",
            fg=GOOD if pad and done == len(present) else WARN)

        # all-axes list (built lazily when a pad appears)
        if pad and set(axes) != set(self._axis_widgets):
            for w in self._axis_rows.winfo_children():
                w.destroy()
            self._axis_widgets = {}
            for i, code in enumerate(sorted(axes)):
                cell = tk.Frame(self._axis_rows, bg=PANEL)
                cell.grid(row=i // 2, column=i % 2, sticky="w", padx=6, pady=1)
                tk.Label(cell, text=js.ABS_NAMES.get(code, hex(code)), bg=PANEL, fg=MUTED, font=(MONO, 9),
                         width=9, anchor="w").pack(side=tk.LEFT)
                b = Bar(cell, width=220, height=12, one_sided=pad["one_sided"].get(code, False))
                b.pack(side=tk.LEFT)
                v = tk.Label(cell, text="", bg=PANEL, fg=TEXT, font=(MONO, 9), width=6)
                v.pack(side=tk.LEFT)
                self._axis_widgets[code] = (b, v)
        for code, (b, v) in self._axis_widgets.items():
            val = axes.get(code, 0.0)
            b.set(val)
            v.configure(text=f"{val:+.2f}")

    def _update_motor_tab(self, mot) -> None:
        if not mot:
            for lbl in self._telem.values():
                lbl.configure(text="—")
            return
        t = mot["telemetry"]
        self._telem["total"].configure(text=str(t["total"]))
        self._telem["per 10 ms"].configure(text=str(t["per10ms"]))
        self._telem["mm/s"].configure(text=str(t["speed"]))
        self._telem["age"].configure(text="no reports" if t["age"] is None else f"{t['age']:.1f} s")
        text = "\n".join(f"${f}#" for f in t["other"])
        if self._other.get("1.0", "end-1c") != text:
            self._other.delete("1.0", tk.END)
            self._other.insert(tk.END, text)

    def _update_log(self) -> None:
        new = [e for e in list(self.core.events) if e[0] > self._log_seen]
        if not new:
            return
        self._log_seen = new[-1][0]
        self._log.configure(state=tk.NORMAL)
        for _seq, ts, level, text in new:
            self._log.insert(tk.END, f"{time.strftime('%H:%M:%S', time.localtime(ts))}  {text}\n", level)
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="never open the motor port (UI + joystick only)")
    ap.add_argument("--dev", help="gamepad /dev/input/eventN (default: auto)")
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if a.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    if not os.environ.get("DISPLAY") and not sys.platform.startswith("win"):
        os.environ["DISPLAY"] = ":0"
    core = RobotCore(Settings.load(), gamepad_dev=a.dev, dry_run=a.dry_run)
    core.start()
    try:
        app = KrRobotApp(core)
    except tk.TclError as e:
        core.shutdown()
        print(f"Cannot open display: {e}")
        return 2
    try:
        app.mainloop()
    finally:
        core.shutdown()
    return 0

if __name__ == "__main__":
    sys.exit(main())
