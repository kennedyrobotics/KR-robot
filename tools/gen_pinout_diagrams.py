#!/usr/bin/env python3
"""Generate the BeagleY-AI 40-pin header diagrams used in docs/wiring-manual.md.

Source data: BeagleBoard.org's pinout site (https://beagleboard.github.io/pinouts/,
repo github.com/beagleboard/pinouts, MIT licence). That site renders the diagram in the
browser from data in src/app/page.tsx, so there is no static image to copy — this script
parses the same data at a pinned commit and draws equivalent SVGs (same pin layout, same
colour scheme), plus a KR-Robot allocation view from docs/wiring-manual.md §3.

    python tools/gen_pinout_diagrams.py                 # download page.tsx at UPSTREAM_COMMIT
    python tools/gen_pinout_diagrams.py --page page.tsx # use a local copy

Writes docs/images/beagley-ai-*.svg and prints the alternate-function table (markdown) used
for the manual's appendix. Stdlib only.
"""

from __future__ import annotations

import argparse
import html
import re
import sys
import urllib.request
from pathlib import Path

UPSTREAM_REPO = "beagleboard/pinouts"
UPSTREAM_COMMIT = "1b4472456540a3766fd5c151ad6610dcfe7dd78f"  # 2025-08-06
PAGE_URL = f"https://raw.githubusercontent.com/{UPSTREAM_REPO}/{UPSTREAM_COMMIT}/src/app/page.tsx"
OUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "images"

# Upstream colour scheme (getPinColor in page.tsx: Solarized palette)
TYPE_COLOURS = {
    "pow3v3": "#B58900", "pow5v": "#DC322F", "gpio": "#859900", "i2c": "#268BD2",
    "spi": "#D33682", "uart": "#6c71c4", "pcm": "#2aa198", "gnd": "#002B36",
}
TYPE_LABELS = {
    "pow3v3": "3V3 power", "pow5v": "5V power", "gnd": "Ground", "gpio": "GPIO",
    "i2c": "I2C", "spi": "SPI", "uart": "UART", "pcm": "PCM / audio",
}

# Bus views like the site's I2C/UART/SPI/PWM filters. NOTE: they are derived from the
# per-pin pin-mux tables (Alt0..Alt9), NOT from the site's summary attributes (pin.I2C/UART/
# SPI/PWM): at the pinned commit those summaries carry Raspberry-Pi placeholders and copy-paste
# slips (e.g. PWM 'SPI0 CE0' on pin 24, 'UART CTS' on pin 3 which has no UART mode).
BUS_VIEWS = {
    "i2c": ("I2C", "#268BD2", r"I2C"),
    "uart": ("UART", "#6c71c4", r"UART"),
    "spi": ("SPI", "#D33682", r"SPI"),
    "pwm": ("PWM", "#cb4b16", r"EHRPWM\d_[AB]$|ECAP\d_IN_APWM_OUT"),
}

# MEASURED on the robot's BeagleY-AI with `gpioinfo` (2026-10-02): header GPIO -> controller line.
# gpiochip0 = 4201000.gpio = MCU_GPIO0, gpiochip1 = 600000.gpio = GPIO0, gpiochip2 = 601000.gpio = GPIO1.
# Used to validate each pin's upstream Alt7 (GPIO) entry; mismatches are flagged, board wins.
MEASURED_GPIO = {
    "GPIO 8": "MCU_GPIO0_0", "GPIO 11": "MCU_GPIO0_2", "GPIO 10": "MCU_GPIO0_3", "GPIO 9": "MCU_GPIO0_4",
    "GPIO 23": "MCU_GPIO0_7", "GPIO 7": "MCU_GPIO0_9", "GPIO 24": "MCU_GPIO0_10", "GPIO 3": "MCU_GPIO0_17",
    "GPIO 2": "MCU_GPIO0_18",
    "GPIO 27": "GPIO0_33", "GPIO 26": "GPIO0_36", "GPIO 4": "GPIO0_38", "GPIO 22": "GPIO0_41", "GPIO 25": "GPIO0_42",
    "GPIO 16": "GPIO1_7", "GPIO 17": "GPIO1_8", "GPIO 21": "GPIO1_9", "GPIO 20": "GPIO1_10", "GPIO 18": "GPIO1_11",
    "GPIO 19": "GPIO1_12", "GPIO 15": "GPIO1_13", "GPIO 14": "GPIO1_14", "GPIO 5": "GPIO1_15", "GPIO 12": "GPIO1_16",
    "GPIO 6": "GPIO1_17", "GPIO 13": "GPIO1_18",
}
# Pins whose whole upstream mux table is a copy of another pin's (detected: same Alt7 as a
# different pin) are not trusted for the bus views.

# KR-Robot allocation (docs/wiring-manual.md §3, PROPOSED unless noted).
KR_COLOURS = {
    "power": "#B58900", "gnd": "#002B36", "bus": "#268BD2", "imu": "#2aa198",
    "us": "#859900", "estop": "#DC322F", "ind": "#cb4b16", "gps": "#6c71c4",
    "reserved": "#93a1a1", "nouse": "#586e75", "free": "#fdf6e3",
}
KR_LEGEND = {
    "bus": "I2C1 sensor bus", "imu": "IMU INT/RST", "us": "Ultrasonic TRIG/ECHO",
    "estop": "Hardware E-stop", "ind": "LED / buzzer", "gps": "GPS UART/PPS (future)",
    "reserved": "Reserved: SPI0 + CAN", "nouse": "Do not use (HAT EEPROM)", "free": "Free",
    "power": "Power", "gnd": "Ground",
}
KR_ALLOC = {
    1: ("power", "3V3: sensors, pull-ups"), 2: ("power", "5V (out only)"),
    3: ("bus", "I2C1 SDA  /dev/i2c-1"), 4: ("power", "5V (out only)"),
    5: ("bus", "I2C1 SCL  /dev/i2c-1"), 6: ("gnd", "GND"),
    7: ("imu", "IMU INT"), 8: ("gps", "UART TX -> GPS RX"),
    9: ("gnd", "GND"), 10: ("gps", "UART RX <- GPS TX"),
    11: ("imu", "IMU RST"), 12: ("gps", "GPS PPS"),
    13: ("estop", "E-stop in (NC, 10k pull-up)"), 14: ("gnd", "GND (E-stop return)"),
    15: ("us", "US1 front-left TRIG"), 16: ("us", "US1 front-left ECHO"),
    17: ("power", "3V3"), 18: ("us", "US2 front-centre TRIG"),
    19: ("reserved", "SPI0 MOSI (CAN)"), 20: ("gnd", "GND"),
    21: ("reserved", "SPI0 MISO (CAN)"), 22: ("reserved", "CAN INT"),
    23: ("reserved", "SPI0 SCLK (CAN)"), 24: ("reserved", "SPI0 CE0 (CAN)"),
    25: ("gnd", "GND"), 26: ("reserved", "SPI0 CE1"),
    27: ("nouse", "HAT EEPROM SDA"), 28: ("nouse", "HAT EEPROM SCL"),
    29: ("us", "US2 front-centre ECHO"), 30: ("gnd", "GND"),
    31: ("us", "US3 front-right TRIG"), 32: ("free", "free (PWM-capable)"),
    33: ("free", "free (PWM-capable)"), 34: ("gnd", "GND"),
    35: ("ind", "Buzzer"), 36: ("us", "US3 front-right ECHO"),
    37: ("us", "US4 rear TRIG"), 38: ("us", "US4 rear ECHO"),
    39: ("gnd", "GND"), 40: ("ind", "Status LED"),
}

ROW_H = 26
TOP = 104
PIN_R = 9


# ----------------------------------------------------------------------------- parsing
def load_page(path: str | None) -> str:
    if path:
        return Path(path).read_text(encoding="utf-8")
    with urllib.request.urlopen(PAGE_URL, timeout=30) as r:
        return r.read().decode("utf-8")


def parse_pins(src: str) -> dict[int, dict]:
    pins: dict[int, dict] = {}
    for arr in ("leftPins", "rightPins"):
        m = re.search(rf"const {arr}: Pin\[\] = \[(.*?)\n\s*\];", src, re.S)
        if not m:
            raise SystemExit(f"could not find {arr} in page.tsx (upstream format changed?)")
        for obj in re.findall(r"\{([^{}]*)\}", m.group(1)):
            # key: either bare (name) or quoted ('1-WIRE'); value: either quoted string or a number
            fields = {k1 or k2: v1 or v2 for k1, k2, v1, v2 in
                      re.findall(r"(?:'([^']+)'|(\w+))\s*:\s*(?:'([^']*)'|(\d+))", obj)}
            pins[int(fields["number"])] = fields
    if sorted(pins) != list(range(1, 41)):
        raise SystemExit(f"expected pins 1..40, got {sorted(pins)}")
    return pins


def parse_alt_functions(src: str) -> dict[str, dict]:
    """'GPIO 2' -> {'functions': [(Alt0, [..]), ...], 'description': ...}"""
    out: dict[str, dict] = {}
    for m in re.finditer(r'"(GPIO \d+)":\s*\{(.*?)\n\s{4}\}', src, re.S):
        body = m.group(2)
        funcs = [(n, re.findall(r'"([^"]+)"', vals))
                 for n, vals in re.findall(r'\{\s*name:\s*"(Alt\d+)",\s*values:\s*\[([^\]]*)\]', body)]
        desc = re.search(r'description:\s*"([^"]*)"', body)
        out[m.group(1)] = {"functions": funcs, "description": desc.group(1) if desc else ""}
    return out


# ----------------------------------------------------------------------------- drawing
def esc(s: str) -> str:
    return html.escape(s, quote=True)


def text_colour(fill: str) -> str:
    r, g, b = (int(fill[i:i + 2], 16) for i in (1, 3, 5))
    return "#002B36" if (0.299 * r + 0.587 * g + 0.114 * b) > 150 else "#ffffff"


def svg_header(pins: dict[int, dict], title: str, subtitle: str,
               style: callable, legend: list[tuple[str, str]]) -> str:
    """style(pin_no, pin) -> (fill, left_or_right_label, dim:bool)"""
    w = 900
    cx_odd, cx_even = w / 2 - 16, w / 2 + 16
    h = TOP + 20 * ROW_H + 30 + ((len(legend) + 3) // 4) * 22 + 46
    o = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h:.0f}" viewBox="0 0 {w} {h:.0f}" '
         f'font-family="DejaVu Sans, Segoe UI, Helvetica, Arial, sans-serif">',
         f'<rect width="{w}" height="{h:.0f}" rx="14" fill="#fdf6e3" stroke="#93a1a1"/>',
         f'<text x="{w / 2}" y="34" font-size="20" font-weight="bold" text-anchor="middle" fill="#002B36">{esc(title)}</text>',
         f'<text x="{w / 2}" y="56" font-size="12" text-anchor="middle" fill="#586e75">{esc(subtitle)}</text>',
         # header body
         f'<rect x="{cx_odd - 22}" y="{TOP - 18}" width="{cx_even - cx_odd + 44}" height="{20 * ROW_H + 10}" '
         f'rx="6" fill="#073642"/>',
         f'<text x="{cx_odd - 30}" y="{TOP - 24}" font-size="11" text-anchor="end" fill="#586e75">pin 1 (square pad) ▸</text>']
    for row in range(20):
        y = TOP + row * ROW_H
        for n, cx, anchor, lx in ((2 * row + 1, cx_odd, "end", cx_odd - 22 - 8),
                                  (2 * row + 2, cx_even, "start", cx_even + 22 + 8)):
            fill, label, dim = style(n, pins[n])
            op = ' opacity="0.28"' if dim else ""
            shape = (f'<rect x="{cx - PIN_R}" y="{y - PIN_R}" width="{2 * PIN_R}" height="{2 * PIN_R}" rx="2"'
                     if n == 1 else f'<circle cx="{cx}" cy="{y}" r="{PIN_R}"')
            o.append(f'{shape} fill="{fill}" stroke="#fdf6e3" stroke-width="1.5"{op}/>')
            o.append(f'<text x="{cx}" y="{y + 3.5}" font-size="8.5" font-weight="bold" text-anchor="middle" '
                     f'fill="{text_colour(fill)}"{op}>{n}</text>')
            # label pill
            tw = 7.0 * len(label) + 14
            px = lx - tw if anchor == "end" else lx
            outline = ' stroke="#93a1a1"' if text_colour(fill) != "#ffffff" else ""   # light pills need an edge
            o.append(f'<rect x="{px:.1f}" y="{y - 10}" width="{tw:.1f}" height="20" rx="10" fill="{fill}"{outline}{op}/>')
            o.append(f'<text x="{px + tw / 2:.1f}" y="{y + 4}" font-size="11.5" text-anchor="middle" '
                     f'fill="{text_colour(fill)}"{op}>{esc(label)}</text>')
    # legend
    ly = TOP + 20 * ROW_H + 26
    for i, (fill, name) in enumerate(legend):
        lx = 40 + (i % 4) * 210
        yy = ly + (i // 4) * 22
        o.append(f'<rect x="{lx}" y="{yy - 10}" width="14" height="14" rx="3" fill="{fill}" stroke="#93a1a1"/>')
        o.append(f'<text x="{lx + 20}" y="{yy + 1}" font-size="12" fill="#002B36">{esc(name)}</text>')
    o.append(f'<text x="{w / 2}" y="{h - 14:.0f}" font-size="10" text-anchor="middle" fill="#839496">'
             f'Data: BeagleBoard.org pinouts (github.com/{UPSTREAM_REPO} @ {UPSTREAM_COMMIT[:7]}, MIT). '
             f'Orientation: GPIO header on the right, HDMI on the left.</text>')
    o.append("</svg>")
    return "\n".join(o)


def name_label(pin: dict) -> str:
    alt = pin.get("altFunction")
    nm = pin["name"].replace("3v3 Power", "3V3").replace("5v Power", "5V").replace("Ground", "GND")
    return f"{nm}  ({alt})" if alt else nm


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--page", help="local copy of src/app/page.tsx (default: download pinned commit)")
    a = ap.parse_args()
    src = load_page(a.page)
    pins = parse_pins(src)
    alts = parse_alt_functions(src)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    def write(name: str, svg: str) -> None:
        (OUT_DIR / name).write_bytes(svg.encode("utf-8"))
        print(f"wrote docs/images/{name}", file=sys.stderr)

    # 1. full header coloured by type (the site's default view)
    legend = [(TYPE_COLOURS[t], TYPE_LABELS[t]) for t in ("pow3v3", "pow5v", "gnd", "gpio", "i2c", "uart", "spi", "pcm")]
    write("beagley-ai-header.svg", svg_header(
        pins, "BeagleY-AI 40-pin header", "Default functions (BCM GPIO names). 3.3 V logic — not 5 V tolerant.",
        lambda n, p: (TYPE_COLOURS[p["type"]], name_label(p), False), legend))

    # validate upstream Alt7 (GPIO) against the board, find untrusted (copied) mux tables
    issues = validate(pins, alts)
    untrusted = copied_tables(alts)                       # whole table duplicated from another pin
    typo = {name: measured for name, _, measured in issues if name not in untrusted}
    for name, upstream, measured in issues:
        kind = "copied table" if name in untrusted else "Alt7 typo"
        print(f"WARNING {name}: upstream Alt7={upstream}, board={measured} ({kind})", file=sys.stderr)

    # 2. per-bus views (the site's I2C / UART / SPI / PWM filters), from the pin-mux tables
    for key, (attr, colour, pattern) in BUS_VIEWS.items():
        def style(n, p, colour=colour, pattern=pattern):
            sigs = [] if p["name"] in untrusted else [
                f"{sig}{'' if mode == 'Alt0' else '*'}"
                for mode, vals in alts.get(p["name"], {}).get("functions", []) for sig in vals
                if re.search(pattern, sig)]
            if sigs:
                return colour, f'{p["name"]}: {", ".join(sigs)}', False
            if p["type"] in ("pow3v3", "pow5v", "gnd"):
                return TYPE_COLOURS[p["type"]], name_label(p), True
            return "#93a1a1", p["name"] + (" (data ?)" if p["name"] in untrusted else ""), True
        write(f"beagley-ai-header-{key}.svg", svg_header(
            pins, f"BeagleY-AI header — {attr}-capable pins (pin-mux)",
            "From the SoC pin-mux tables. No * = default (mode 0); * = alternate mode, needs an overlay.",
            style, [(colour, f"{attr} signal available"), ("#93a1a1", "other GPIO")]))

    # 3. KR-Robot allocation (docs/wiring-manual.md §3)
    write("kr-robot-header-allocation.svg", svg_header(
        pins, "KR-Robot header allocation (PROPOSED)", "From docs/wiring-manual.md §3 — status: proposed until bench-verified.",
        lambda n, p: (KR_COLOURS[KR_ALLOC[n][0]], f'{p["name"].replace(" Power", "").replace("Ground", "GND")} · {KR_ALLOC[n][1]}'
                      if p["type"] not in ("pow3v3", "pow5v", "gnd") else KR_ALLOC[n][1], False),
        [(KR_COLOURS[k], v) for k, v in KR_LEGEND.items()]))

    # 4. alternate-function appendix (markdown, stdout)
    print("| Pin | BCM | SoC ball | GPIO signal (board) | Pin-mux modes (upstream) |")
    print("|---|---|---|---|---|")
    for n in range(1, 41):
        p = pins[n]
        if not p["name"].startswith("GPIO"):
            continue
        fx = alts.get(p["name"], {}).get("functions", [])
        cell = "<br/>".join(f"{mode}: {', '.join(v)}" for mode, v in fx) or "—"
        if p["name"] in untrusted:
            cell = f"**upstream data error**: copy of another pin's table (Alt7 says {alt7(alts, p['name'])}); use GPIO only"
        elif p["name"] in typo:
            cell += f"<br/>(upstream Alt7 reads {alt7(alts, p['name'])}; corrected to {typo[p['name']]} from the board)"
        board = MEASURED_GPIO.get(p["name"], "— (HAT EEPROM, not exported)")
        print(f'| {n} | {p["name"].replace("GPIO ", "GPIO")} | {p.get("so_c", "—")} | {board} | {cell} |')
    return 0


def alt7(alts: dict, name: str) -> str:
    for mode, vals in alts.get(name, {}).get("functions", []):
        if mode == "Alt7" and vals:
            return vals[0]
    return "?"


def copied_tables(alts: dict) -> set[str]:
    """Pins whose full mux table is identical to an earlier pin's table (copy-paste in upstream)."""
    seen: dict[tuple, str] = {}
    dup = set()
    for name, data in alts.items():
        key = tuple((m, tuple(v)) for m, v in data.get("functions", []))
        if key and key in seen and name in MEASURED_GPIO and alt7(alts, name) != MEASURED_GPIO[name]:
            dup.add(name)          # the copy is the one whose GPIO disagrees with the board
        seen.setdefault(key, name)
    return dup


def validate(pins: dict, alts: dict) -> list[tuple[str, str, str]]:
    """Pins whose upstream Alt7 GPIO name disagrees with the board's measured line."""
    bad = []
    for n in range(1, 41):
        name = pins[n]["name"]
        if name in MEASURED_GPIO and alt7(alts, name) != MEASURED_GPIO[name]:
            bad.append((name, alt7(alts, name), MEASURED_GPIO[name]))
    return bad


if __name__ == "__main__":
    sys.exit(main())
