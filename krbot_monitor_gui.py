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

# Controller tab: linux evdev codes as krbot reports them (after its per-pad button remap). Defined here,
# not imported from krc.joystick, because that module needs fcntl and this window also runs on Windows.
PAD_BUTTONS = (  # (name, code)
    ("A", 0x130), ("B", 0x131), ("X", 0x134), ("Y", 0x133), ("LB", 0x136), ("RB", 0x137),
    ("BACK", 0x13A), ("START", 0x13B), ("HOME", 0x13C), ("LS", 0x13D), ("RS", 0x13E))
PAD_ROLES = {"LB": "deadman", "START": "arm", "B": "e-stop", "HOME": "e-stop"}
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_GAS, ABS_BRAKE, ABS_HAT0X, ABS_HAT0Y = 0, 1, 2, 3, 4, 5, 9, 10, 16, 17
FULL = 0.9             # axis checklist: a stick/trigger "reaches full travel" past this


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
        self._build_controller_tab()
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
        self._c_mot = {k: card_row(mot, k, width=11) for k in ("status", "board", "driver", "watchdog")}
        bat = section(cards, "Battery (motor board)", side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._c_bat = {k: card_row(bat, k, width=9) for k in ("voltage", "per cell", "charge", "arming")}

        out = section(f, "Track output (PWM counts, % of the ±3600 full scale)")
        self._out_bars = {}
        for side in ("Left (M1)", "Right (M2)"):
            r = tk.Frame(out, bg=PANEL)
            r.pack(fill=tk.X, pady=3)
            tk.Label(r, text=side, bg=PANEL, fg=MUTED, font=(UI, 10), width=12, anchor="w").pack(side=tk.LEFT)
            b = Bar(r, width=560, height=22)
            b.pack(side=tk.LEFT, padx=6)
            v = tk.Label(r, text="0", bg=PANEL, fg=TEXT, font=(MONO, 12, "bold"), width=13, anchor="e")
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

    # ------------------------------------------------------------------ controller
    def _build_controller_tab(self) -> None:
        """Every button and axis on the pad, live, plus a press-each-one checklist for maintenance."""
        f = ttk.Frame(self._nb)
        self._nb.add(f, text="Controller")
        top = tk.Frame(f, bg=BG)
        top.pack(fill=tk.X, padx=8, pady=(6, 0))
        self._pad_info = tk.Label(top, text="", bg=BG, fg=MUTED, font=(MONO, 10), anchor="w")
        self._pad_info.pack(side=tk.LEFT, fill=tk.X, expand=True)
        button(top, "Reset checklist", self._pad_reset).pack(side=tk.RIGHT)

        body = tk.Frame(f, bg=BG)
        body.pack(fill=tk.BOTH, expand=True, padx=6)
        pic = section(body, "Live — lit while pressed", side=tk.LEFT, fill=tk.Y)
        cv = self._pad_cv = tk.Canvas(pic, width=560, height=300, bg=ENTRY_BG, highlightthickness=0)
        cv.pack()
        cv.create_polygon(90, 70, 470, 70, 520, 150, 510, 270, 440, 285, 380, 235, 180, 235, 120, 285, 50, 270,
                          40, 150, smooth=True, fill=PANEL, outline=DIM)
        self._pad_items: dict[str, int] = {}

        def btn(name: str, x: float, y: float, r: float, label: str | None = None) -> None:
            self._pad_items[name] = cv.create_oval(x - r, y - r, x + r, y + r, fill=ENTRY_BG, outline=MUTED, width=2)
            cv.create_text(x, y, text=label or name, fill=TEXT, font=(UI, 8 if r < 14 else 10, "bold"))

        def rect(name: str, x0: float, y0: float, x1: float, y1: float, label: str) -> None:
            self._pad_items[name] = cv.create_rectangle(x0, y0, x1, y1, fill=ENTRY_BG, outline=MUTED, width=2)
            cv.create_text((x0 + x1) / 2, (y0 + y1) / 2, text=label, fill=TEXT, font=(UI, 9, "bold"))

        for side, x0 in (("L", 80), ("R", 380)):   # triggers (fill bar) above bumpers
            cv.create_rectangle(x0, 8, x0 + 100, 28, outline=MUTED, width=2)
            self._pad_items[f"{side}T"] = cv.create_rectangle(x0, 8, x0, 28, fill=ACCENT, width=0)
            cv.create_text(x0 + 50, 18, text=f"{side}T", fill=TEXT, font=(UI, 9, "bold"))
            rect(f"{side}B", x0, 36, x0 + 100, 58, f"{side}B")
        for name, cx, cy in (("LS", 150, 135), ("RS", 340, 200)):   # sticks: ring = click, dot = position
            self._pad_items[name] = cv.create_oval(cx - 36, cy - 36, cx + 36, cy + 36, fill=ENTRY_BG, outline=MUTED, width=2)
            self._pad_items[name + "_dot"] = cv.create_oval(cx - 9, cy - 9, cx + 9, cy + 9, fill=ACCENT, outline="")
            cv.create_text(cx, cy + 48, text=name, fill=MUTED, font=(UI, 8))
        for name, dx, dy in (("D_UP", 0, -1), ("D_DOWN", 0, 1), ("D_LEFT", -1, 0), ("D_RIGHT", 1, 0)):
            x, y = 220 + dx * 20, 200 + dy * 20
            self._pad_items[name] = cv.create_rectangle(x - 10, y - 10, x + 10, y + 10, fill=ENTRY_BG, outline=MUTED, width=2)
        cv.create_text(220, 238, text="D-pad", fill=MUTED, font=(UI, 8))
        btn("Y", 420, 105, 15)
        btn("X", 390, 135, 15)
        btn("B", 450, 135, 15)
        btn("A", 420, 165, 15)
        btn("BACK", 235, 125, 12, "⧉")
        btn("HOME", 280, 95, 16, "⌂")
        btn("START", 325, 125, 12, "≡")
        self._pad_axes_lbl = tk.Label(pic, text="", bg=PANEL, fg=TEXT, font=(MONO, 9), justify=tk.LEFT, anchor="w")
        self._pad_axes_lbl.pack(fill=tk.X, pady=(6, 0))
        self._pad_other_lbl = tk.Label(pic, text="", bg=PANEL, fg=WARN, font=(MONO, 9), anchor="w")
        self._pad_other_lbl.pack(fill=tk.X)

        chk = section(body, "Maintenance checklist — press / move each one", side=tk.LEFT, fill=tk.BOTH, expand=True)
        for col, (text, w) in enumerate((("control", 9), ("role", 8), ("now", 5), ("presses", 7), ("ok", 4))):
            tk.Label(chk, text=text, bg=PANEL, fg=DIM, font=(UI, 9, "bold"), width=w, anchor="w").grid(row=0, column=col)
        self._pad_rows: dict[str, tuple[tk.Label, tk.Label, tk.Label]] = {}
        rows = [n for n, _ in PAD_BUTTONS] + ["LT", "RT", "L-stick", "R-stick", "D-pad"]
        for i, name in enumerate(rows, start=1):
            role = PAD_ROLES.get(name, "full travel" if name in ("LT", "RT") else "4 ways" if "stick" in name or name == "D-pad" else "")
            tk.Label(chk, text=name, bg=PANEL, fg=TEXT, font=(MONO, 10), width=9, anchor="w").grid(row=i, column=0)
            tk.Label(chk, text=role, bg=PANEL, fg=WARN if role in ("deadman", "arm", "e-stop") else MUTED,
                     font=(UI, 9), width=8, anchor="w").grid(row=i, column=1)
            cells = tuple(tk.Label(chk, text="", bg=PANEL, fg=TEXT, font=(MONO, 10), width=w, anchor="w")
                          for w in (5, 7, 4))
            for col, c in enumerate(cells, start=2):
                c.grid(row=i, column=col, pady=1)
            self._pad_rows[name] = cells
        self._pad_summary = tk.Label(chk, text="", bg=PANEL, fg=MUTED, font=(UI, 10, "bold"), anchor="w")
        self._pad_summary.grid(row=len(rows) + 1, column=0, columnspan=5, sticky="w", pady=(8, 0))
        tk.Label(f, text="Press counts come from krbot (a quick tap between 10 Hz snapshots still counts). Sticks, "
                         "triggers and D-pad are sampled at 10 Hz — hold each direction briefly. Live view only: "
                         "nothing here drives the robot.",
                 bg=BG, fg=DIM, font=(UI, 9, "italic"), wraplength=1100, justify=tk.LEFT).pack(anchor="w", padx=12, pady=(0, 4))
        self._pad_reset()

    def _pad_reset(self) -> None:
        pad = self.status.get("pad", {}) if self.status else {}
        self._pad_base = {int(k): v[1] for k, v in pad.get("buttons", {}).items()}
        self._pad_seen: set[str] = set()   # axis directions reached since reset, e.g. "L-stick+x", "LT", "D-pad-y"

    def _update_controller(self, live: bool, pad: dict, connected: bool) -> None:
        cv = self._pad_cv
        keys = set(pad.get("keys", [])) if live and connected else set()
        btns = {int(k): v for k, v in pad.get("buttons", {}).items()} if live and connected else {}
        axes = {int(k): v for k, v in pad.get("axes", {}).items()} if live and connected else {}
        remap = "  [button remap active]" if pad.get("remap") else ""
        self._pad_info.configure(text=f"{(self.status.get('gamepad') or {}).get('name') or 'no gamepad'}  "
                                      f"id {pad.get('id') or '—'}{remap}" if live and connected else
                                 "no gamepad connected to krbot" if live else "no live data from krbot")

        n_ok = n_total = 0
        for name, code in PAD_BUTTONS:
            down, count = btns.get(code, (0, 0))
            if count < self._pad_base.get(code, 0):          # pad reconnected: krbot's counts restarted
                self._pad_base[code] = 0
            pressed = count - self._pad_base.get(code, 0)
            present = code in keys
            role = PAD_ROLES.get(name)
            lit = BAD if role == "e-stop" else GOOD
            cv.itemconfigure(self._pad_items[name], fill=lit if down else ENTRY_BG,
                             outline=MUTED if present or not keys else DIM, dash="" if present or not keys else (3, 3))
            now, cnt, ok = self._pad_rows[name]
            now.configure(text="●" if down else "○", fg=lit if down else DIM)
            cnt.configure(text=str(pressed) if present else "absent", fg=TEXT if present else DIM)
            ok.configure(text="✓" if pressed else "", fg=GOOD)
            if present:
                n_total += 1
                n_ok += bool(pressed)

        def axis(*codes: int) -> float:
            return next((axes[c] for c in codes if c in axes), 0.0)

        lt, rt = axis(ABS_Z, ABS_BRAKE), axis(ABS_RZ, ABS_GAS)
        for side, v in (("L", lt), ("R", rt)):
            x0 = 80 if side == "L" else 380
            cv.coords(self._pad_items[f"{side}T"], x0, 8, x0 + 100 * max(0.0, min(1.0, v)), 28)
            if v > FULL:
                self._pad_seen.add(f"{side}T")
        sticks = {"L-stick": ("LS", 150, 135, axis(ABS_X), axis(ABS_Y)),
                  "R-stick": ("RS", 340, 200, axis(ABS_RX), axis(ABS_RY))}
        for row, (name, cx, cy, x, y) in sticks.items():
            cv.coords(self._pad_items[name + "_dot"], cx + x * 27 - 9, cy + y * 27 - 9, cx + x * 27 + 9, cy + y * 27 + 9)
            for ax, v in (("x", x), ("y", y)):
                if abs(v) > FULL:
                    self._pad_seen.add(f"{row}{'+' if v > 0 else '-'}{ax}")
        hx, hy = axis(ABS_HAT0X), axis(ABS_HAT0Y)
        for name, on in (("D_UP", hy < -0.5), ("D_DOWN", hy > 0.5), ("D_LEFT", hx < -0.5), ("D_RIGHT", hx > 0.5)):
            cv.itemconfigure(self._pad_items[name], fill=GOOD if on else ENTRY_BG)
            if on:
                self._pad_seen.add(name)

        def axis_row(row: str, now_text: str, need: list[str], arrows: str) -> None:
            nonlocal n_ok, n_total
            got = [n for n in need if n in self._pad_seen]
            now, cnt, ok = self._pad_rows[row]
            now.configure(text=now_text, fg=TEXT)
            cnt.configure(text="".join(a if n in self._pad_seen else "·" for a, n in zip(arrows, need)), fg=TEXT)
            ok.configure(text="✓" if len(got) == len(need) else "", fg=GOOD)
            if axes:
                n_total += 1
                n_ok += len(got) == len(need)

        axis_row("LT", f"{lt:.2f}", ["LT"], "■")
        axis_row("RT", f"{rt:.2f}", ["RT"], "■")
        for row in sticks:
            axis_row(row, "", [f"{row}-y", f"{row}+y", f"{row}-x", f"{row}+x"], "↑↓←→")
        axis_row("D-pad", "", ["D_UP", "D_DOWN", "D_LEFT", "D_RIGHT"], "↑↓←→")

        self._pad_axes_lbl.configure(text=(
            f"L  x {axis(ABS_X):+.3f}  y {axis(ABS_Y):+.3f}     R  x {axis(ABS_RX):+.3f}  y {axis(ABS_RY):+.3f}\n"
            f"LT {lt:.3f}   RT {rt:.3f}   hat {hx:+.0f},{hy:+.0f}   (centred sticks should read ~0.000: drift check)")
            if axes else "")
        known = {c for _, c in PAD_BUTTONS}
        other = sorted(c for c in keys if c not in known)
        held = [f"0x{c:03x}" + ("●" if btns.get(c, (0, 0))[0] else "") for c in other]
        self._pad_other_lbl.configure(text=f"other key codes on this device (not used by teleop): {' '.join(held)}"
                                      if held else "")
        self._pad_summary.configure(
            text=f"{n_ok} / {n_total} confirmed" + ("  — ALL OK" if n_total and n_ok == n_total else ""),
            fg=GOOD if n_total and n_ok == n_total else MUTED)

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
        bat, board = s.get("battery") or {}, s.get("board") or {}
        bat_v = bat.get("v")
        self._links_lbl.configure(text=(
            f"server: {'OK' if c.connected else 'DOWN'} {c.host}:{c.port}    "
            f"gamepad: {'OK' if pad.get('connected') else 'none'}    "
            f"motors: {'OK' if mot.get('healthy') else 'FAULT' if s else '-'}    "
            f"battery: {f'{bat_v:.1f} V' if bat_v is not None else '-'}    "
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
        alive, age = board.get("alive"), board.get("reply_age_s")
        self._c_mot["board"].configure(
            text=("n/a (no self-report)" if alive is None else
                  f"replying ({age:.1f} s ago)" if alive else
                  f"NOT replying{f' for {age:.0f} s' if age is not None else ''}") if s and board else "—",
            fg=TEXT if alive is None else GOOD if alive else BAD)
        state = bat.get("state", "unknown")
        colour = {"ok": GOOD, "warn": WARN, "low": BAD}.get(state, DIM)
        self._c_bat["voltage"].configure(text=f"{bat_v:.1f} V  ({state})" if bat_v is not None else
                                         ("no reading" if s else "—"), fg=colour)
        self._c_bat["per cell"].configure(
            text=f"{bat['cell_v']:.2f} V x {bat.get('cells', '?')}S" if bat.get("cell_v") is not None else "—", fg=colour)
        self._c_bat["charge"].configure(
            text=f"~{bat['pct']:.0f} %  (resting estimate)" if bat.get("pct") is not None else "—", fg=colour)
        self._c_bat["arming"].configure(
            text=(f"BLOCKED: below {bat.get('low_v', 0):.1f} V" if bat.get("arm_blocked") else
                  f"allowed (warn {bat.get('warn_v', 0):.1f} / low {bat.get('low_v', 0):.1f} V)") if bat else "—",
            fg=BAD if bat.get("arm_blocked") else TEXT)
        wd = s.get("watchdog", {})
        self._c_mot["watchdog"].configure(text=f"{wd.get('trips', '-')} trips / {wd.get('timeout_ms', '-')} ms",
                                          fg=BAD if wd.get("trips") else TEXT)

        out = s.get("out", {})
        scale = max(1, s.get("max_output", 1800))
        full = max(1, s.get("pwm_full_scale", 3600))
        for (bar, lbl), v in zip(self._out_bars.values(), (out.get("left", 0), out.get("right", 0))):
            bar.set(v / scale, GOOD if v else DIM)
            lbl.configure(text=f"{v:+d} {100 * v / full:+4.0f}%")

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
        self._update_controller(live, s.get("pad", {}), bool(pad.get("connected")))


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
