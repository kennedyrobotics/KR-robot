"""Shared tkinter look for the KR-Robot desktop apps (KR-Robot Control, KR-bot Monitor).

Dark palette from the atr-viu-emulator dashboard plus the small widgets both apps use.
Pure tkinter, no Linux-only imports, so it also runs on Windows (e.g. the monitor on the PC).
"""

import sys
import tkinter as tk
from tkinter import ttk

BG = "#1e1e2e"
PANEL = "#313244"
ENTRY_BG = "#11111b"
TEXT = "#cdd6f4"
MUTED = "#a6adc8"
ACCENT = "#89b4fa"
GOOD = "#a6e3a1"
BAD = "#f38ba8"
WARN = "#fab387"
DIM = "#6c7086"
ESTOP_RED = "#d20f39"

if sys.platform.startswith("win"):
    UI, MONO = "Segoe UI", "Consolas"
else:
    UI, MONO = "DejaVu Sans", "DejaVu Sans Mono"

MODE_COLOURS = {"ARMED": GOOD, "DISARMED": WARN, "ESTOP": BAD}


def apply_style(root: tk.Tk) -> None:
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure(".", background=BG, foreground=TEXT, fieldbackground=ENTRY_BG)
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=[14, 5], font=(UI, 10, "bold"), background=PANEL, foreground=MUTED)
    style.map("TNotebook.Tab", background=[("selected", ACCENT)], foreground=[("selected", BG)])
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=TEXT, font=(UI, 10))
    style.configure("Title.TLabel", font=(UI, 12, "bold"), foreground=ACCENT)
    style.configure("TCombobox", fieldbackground=ENTRY_BG, foreground=TEXT, background=PANEL)


def section(parent: tk.Widget, title: str, **pack) -> tk.LabelFrame:
    lf = tk.LabelFrame(parent, text=f" {title} ", bg=PANEL, fg=ACCENT, font=(UI, 10, "bold"), padx=10, pady=6)
    lf.pack(**({"fill": tk.X, "padx": 6, "pady": 4} | pack))
    return lf


def card_row(parent: tk.Widget, label: str, initial: str = "—", width: int = 14) -> tk.Label:
    row = tk.Frame(parent, bg=PANEL)
    row.pack(fill=tk.X, pady=1)
    tk.Label(row, text=label, bg=PANEL, fg=MUTED, font=(UI, 9), width=width, anchor="w").pack(side=tk.LEFT)
    val = tk.Label(row, text=initial, bg=PANEL, fg=TEXT, font=(MONO, 10), anchor="w")
    val.pack(side=tk.LEFT, fill=tk.X)
    return val


def button(parent, text, cmd, colour=ACCENT, **kw) -> tk.Button:
    return tk.Button(parent, text=text, command=cmd, bg=colour, fg=BG, activebackground=TEXT,
                     font=(UI, 9, "bold"), relief=tk.FLAT, padx=10, pady=3,
                     takefocus=0, **kw)   # SPACE must only ever mean E-STOP


class Bar(tk.Canvas):
    """Horizontal bar: centred (-1..1) or one-sided (0..1)."""

    def __init__(self, parent, width=220, height=16, one_sided=False):
        super().__init__(parent, width=width, height=height, bg=ENTRY_BG, highlightthickness=0)
        self.w, self.h, self.one_sided = width, height, one_sided
        self.fill = self.create_rectangle(0, 0, 0, height, fill=ACCENT, width=0)
        if not one_sided:
            self.create_line(width / 2, 0, width / 2, height, fill=DIM)

    def set(self, v: float, colour: str = ACCENT) -> None:
        v = max(-1.0, min(1.0, v))
        if self.one_sided:
            self.coords(self.fill, 0, 0, max(0.0, v) * self.w, self.h)
        else:
            mid = self.w / 2
            self.coords(self.fill, min(mid, mid + v * mid), 0, max(mid, mid + v * mid), self.h)
        self.itemconfigure(self.fill, fill=colour)


class StickView(tk.Canvas):
    def __init__(self, parent, size=150):
        super().__init__(parent, width=size, height=size, bg=ENTRY_BG, highlightthickness=0)
        self.s = size
        r = size / 2 - 6
        c = size / 2
        self.create_oval(c - r, c - r, c + r, c + r, outline=DIM)
        self.create_line(c, 6, c, size - 6, fill=PANEL)
        self.create_line(6, c, size - 6, c, fill=PANEL)
        self.dot = self.create_oval(c - 7, c - 7, c + 7, c + 7, fill=ACCENT, outline="")
        self.r = r

    def set(self, x: float, y: float, active: bool) -> None:
        c = self.s / 2
        px, py = c + x * self.r, c + y * self.r
        self.coords(self.dot, px - 7, py - 7, px + 7, py + 7)
        self.itemconfigure(self.dot, fill=GOOD if active else ACCENT)
