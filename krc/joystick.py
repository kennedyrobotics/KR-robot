"""Gamepad input via raw Linux evdev — no python-evdev / libevdev-dev dependency.

Bring-up / reference implementation of the L5 InputDriver. Works for the SN2403 in any
mode that Linux exposes as a gamepad: wired XInput (xpad), Bluetooth "Xbox Wireless
Controller" (hid-microsoft / xpadneo), PS4 (hid-playstation), Switch (hid-nintendo).
Axis ranges are read from the kernel (EVIOCGABS) so every mode normalises to -1..+1.

Link loss (unplug, BT drop, pad auto-sleep) surfaces as OSError(ENODEV) from read();
callers must treat that as a safe-stop event (design note §6.2).
"""

from __future__ import annotations

import errno
import fcntl
import glob
import os
import select
import struct
from dataclasses import dataclass, field

# --- linux/input.h constants ------------------------------------------------
EV_SYN, EV_KEY, EV_ABS = 0x00, 0x01, 0x03
EV_MAX = 0x1F
KEY_MAX = 0x2FF
ABS_MAX = 0x3F

ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05
ABS_GAS, ABS_BRAKE = 0x09, 0x0A
ABS_HAT0X, ABS_HAT0Y = 0x10, 0x11

BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2 = 0x136, 0x137, 0x138, 0x139
BTN_SELECT, BTN_START, BTN_MODE = 0x13A, 0x13B, 0x13C
BTN_THUMBL, BTN_THUMBR = 0x13D, 0x13E

ABS_NAMES = {ABS_X: "LX", ABS_Y: "LY", ABS_Z: "LT", ABS_RX: "RX", ABS_RY: "RY", ABS_RZ: "RT",
             ABS_GAS: "RT(gas)", ABS_BRAKE: "LT(brake)", ABS_HAT0X: "DPAD_X", ABS_HAT0Y: "DPAD_Y"}
BTN_NAMES = {BTN_SOUTH: "A", BTN_EAST: "B", BTN_NORTH: "Y", BTN_WEST: "X",
             BTN_TL: "LB", BTN_TR: "RB", BTN_TL2: "LT_btn", BTN_TR2: "RT_btn",
             BTN_SELECT: "BACK", BTN_START: "START", BTN_MODE: "HOME",
             BTN_THUMBL: "LS", BTN_THUMBR: "RS"}

# struct input_event on 64-bit Linux: struct timeval (2x long) + u16 type + u16 code + s32 value
_EVENT = struct.Struct("qqHHi")
# struct input_absinfo: value, minimum, maximum, fuzz, flat, resolution
_ABSINFO = struct.Struct("6i")


def _ioc(direction: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (ord("E") << 8) | nr


_IOC_WRITE, _IOC_READ = 1, 2


def EVIOCGNAME(length: int) -> int:
    return _ioc(_IOC_READ, 0x06, length)


def EVIOCGBIT(ev: int, length: int) -> int:
    return _ioc(_IOC_READ, 0x20 + ev, length)


def EVIOCGABS(axis: int) -> int:
    return _ioc(_IOC_READ, 0x40 + axis, _ABSINFO.size)


EVIOCGRAB = _ioc(_IOC_WRITE, 0x90, 4)
EVIOCGID = _ioc(_IOC_READ, 0x02, 8)            # struct input_id: bustype, vendor, product, version

# Force feedback (rumble). struct ff_effect is 48 bytes on 64-bit Linux:
#   u16 type, s16 id, u16 direction, ff_trigger(u16,u16), ff_replay(u16 length, u16 delay),
#   2 pad, then an 8-byte-aligned 32-byte union; ff_rumble_effect = (u16 strong, u16 weak) at its start.
EV_FF = 0x15
FF_RUMBLE = 0x50
FF_MAX = 0x7F
_FF_EFFECT = struct.Struct("HhHHHHH2xHH28x")
EVIOCSFF = _ioc(_IOC_WRITE, 0x80, _FF_EFFECT.size)
EVIOCRMFF = _ioc(_IOC_WRITE, 0x81, 4)

BUS_NAMES = {0x03: "USB", 0x05: "Bluetooth", 0x06: "virtual"}
EV_NAMES = {EV_SYN: "SYN", EV_KEY: "KEY", EV_ABS: "ABS", 0x04: "MSC", EV_FF: "FF"}


def _bits(fd: int, ev: int, max_code: int) -> set[int]:
    buf = bytearray((max_code + 8) // 8)
    fcntl.ioctl(fd, EVIOCGBIT(ev, len(buf)), buf)
    return {i for i in range(max_code + 1) if buf[i // 8] & (1 << (i % 8))}


TRIGGER_AXES = {ABS_Z, ABS_RZ, ABS_GAS, ABS_BRAKE}


@dataclass
class AxisInfo:
    code: int
    minimum: int
    maximum: int
    flat: int
    fuzz: int = 0

    @property
    def one_sided(self) -> bool:
        # Triggers: xpad / hid-playstation report ABS_Z/ABS_RZ 0..255, BT Xbox 0..1023.
        # Xbox-over-BT without xpadneo puts the *right stick* on Z/RZ at 0..65535 — the
        # range check keeps that centred. PS4 sticks are 0..255 but on X/Y/RX/RY, so centred.
        return self.code in TRIGGER_AXES and self.minimum >= 0 and self.maximum <= 1023

    def normalise(self, raw: int) -> float:
        """Map raw to -1..+1 for centred sticks/hats, 0..1 for one-sided triggers."""
        lo, hi = self.minimum, self.maximum
        if hi <= lo:
            return 0.0
        if self.one_sided:
            return max(0.0, min(1.0, (raw - lo) / (hi - lo)))
        centre = (lo + hi) / 2.0
        half = (hi - lo) / 2.0
        v = (raw - centre) / half
        return max(-1.0, min(1.0, v))


@dataclass
class PadState:
    axes: dict[int, float] = field(default_factory=dict)
    buttons: dict[int, bool] = field(default_factory=dict)

    def axis(self, code: int) -> float:
        return self.axes.get(code, 0.0)

    def button(self, code: int) -> bool:
        return self.buttons.get(code, False)


def device_name(path: str) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    try:
        buf = bytearray(256)
        fcntl.ioctl(fd, EVIOCGNAME(len(buf)), buf)
        return buf.split(b"\0", 1)[0].decode(errors="replace")
    finally:
        os.close(fd)


def is_gamepad(path: str) -> bool:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        evs = _bits(fd, 0, EV_MAX)
        if EV_KEY not in evs or EV_ABS not in evs:
            return False
        keys = _bits(fd, EV_KEY, KEY_MAX)
        return BTN_SOUTH in keys
    except OSError:
        return False
    finally:
        os.close(fd)


def find_gamepads() -> list[tuple[str, str]]:
    """[(path, name)] for every evdev node that looks like a gamepad."""
    found = []
    for path in sorted(glob.glob("/dev/input/event*"), key=lambda p: int(p.rsplit("event", 1)[1])):
        if is_gamepad(path):
            try:
                found.append((path, device_name(path)))
            except OSError:
                pass
    return found


class Gamepad:
    def __init__(self, path: str | None = None, grab: bool = True):
        if path is None:
            pads = find_gamepads()
            if not pads:
                raise FileNotFoundError(errno.ENODEV, "no gamepad found in /dev/input/event*")
            path = pads[0][0]
        self.path = path
        # read-write so rumble can be played; fall back to read-only
        try:
            self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
            self.writable = True
        except PermissionError:
            self.fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            self.writable = False
        self.name = device_name(path)
        buf = bytearray(8)
        fcntl.ioctl(self.fd, EVIOCGID, buf)
        self.bustype, self.vendor, self.product, self.version = struct.unpack("4H", buf)
        self.keys = _bits(self.fd, EV_KEY, KEY_MAX)
        try:
            self.ff = _bits(self.fd, EV_FF, FF_MAX) if EV_FF in _bits(self.fd, 0, EV_MAX) else set()
        except OSError:
            self.ff = set()
        self.raw_axes: dict[int, int] = {}
        if grab:
            # exclusive: stop the desktop treating stick moves as mouse/keys
            try:
                fcntl.ioctl(self.fd, EVIOCGRAB, 1)
            except OSError:
                pass
        self.axis_info: dict[int, AxisInfo] = {}
        self.state = PadState()
        for code in _bits(self.fd, EV_ABS, ABS_MAX):
            buf = bytearray(_ABSINFO.size)
            fcntl.ioctl(self.fd, EVIOCGABS(code), buf)
            value, lo, hi, _fuzz, flat, _res = _ABSINFO.unpack(buf)
            info = AxisInfo(code, lo, hi, flat)
            info.fuzz = _fuzz
            self.axis_info[code] = info
            self.raw_axes[code] = value
            self.state.axes[code] = info.normalise(value)

    @property
    def can_rumble(self) -> bool:
        return self.writable and FF_RUMBLE in self.ff

    def rumble(self, strong: float, weak: float, duration_ms: int = 300) -> None:
        """Play one rumble effect (0..1 per motor). Useful as arm / e-stop haptic feedback."""
        if not self.can_rumble:
            raise OSError(errno.ENOTSUP, "rumble not supported or device not writable")
        effect = bytearray(_FF_EFFECT.pack(FF_RUMBLE, -1, 0, 0, 0, int(duration_ms), 0,
                                           int(max(0.0, min(1.0, strong)) * 0xFFFF),
                                           int(max(0.0, min(1.0, weak)) * 0xFFFF)))
        fcntl.ioctl(self.fd, EVIOCSFF, effect)          # kernel writes the assigned id back
        effect_id = struct.unpack_from("h", effect, 2)[0]
        os.write(self.fd, _EVENT.pack(0, 0, EV_FF, effect_id, 1))
        self._last_ff_id = effect_id

    def rumble_cleanup(self) -> None:
        eid = getattr(self, "_last_ff_id", None)
        if eid is not None and self.fd >= 0:
            try:
                fcntl.ioctl(self.fd, EVIOCRMFF, eid)
            except OSError:
                pass
            self._last_ff_id = None

    def close(self) -> None:
        self.rumble_cleanup()
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def poll(self, timeout: float = 0.0) -> list[tuple[int, int, int]]:
        """Drain pending events into self.state. Returns raw (type, code, value) list.

        Raises OSError (typically ENODEV) when the device disappears — treat as link loss.
        """
        r, _, _ = select.select([self.fd], [], [], timeout)
        if not r:
            return []
        try:
            data = os.read(self.fd, _EVENT.size * 64)
        except BlockingIOError:
            return []
        if not data:
            raise OSError(errno.ENODEV, "gamepad EOF")
        events = []
        for off in range(0, len(data) - _EVENT.size + 1, _EVENT.size):
            _s, _us, etype, code, value = _EVENT.unpack_from(data, off)
            if etype == EV_ABS and code in self.axis_info:
                self.raw_axes[code] = value
                self.state.axes[code] = self.axis_info[code].normalise(value)
            elif etype == EV_KEY:
                self.state.buttons[code] = value != 0
            if etype != EV_SYN:
                events.append((etype, code, value))
        return events
