"""Yahboom 4-Channel Encoder Motor Drive Module (YB-ESF01-V2.0) — USB-serial driver.

Bring-up / reference implementation of the L1 YahboomMotorControllerDriver.
Transport is the on-board CH340K USB-UART (see kr-robot-motor-control-and-joystick.md §2.3).

Protocol (from Yahboom USART lessons, 115200 8N1, ASCII frames "$cmd:args#"):
    $mtype:N#              motor type 1..4 (4 = no-encoder TT profile, used for our 2-wire 33GB-520s)
    $mphase:N#             gearbox reduction ratio
    $mline:N#              encoder magnetic-ring line count
    $wdiameter:F#          wheel diameter, mm
    $deadzone:N#           motor dead zone (PWM counts)
    $upload:A,B,C#         enable reports: A=$MAll (total pulses), B=$MTEP (pulses/10 ms), C=$MSPD (mm/s)
    $pwm:m1,m2,m3,m4#      open-loop PWM per channel, -3600..3600
    $spd:m1,m2,m3,m4#      closed-loop speed (encoders only), -1000..1000
    $read_vol#             battery voltage -> "$Battery:7.40V#"
    $read_flash#           stored settings
Source: Yahboom "1.2 Control command.pdf" (github.com/YahboomTechnology/4-Channel-Motor-Drive-Module).
The board reports no motor current, temperature or fault flags.

Board reports arrive as "$MAll:a, b, c, d#", "$MTEP:...#", "$MSPD:...#", "$Battery:7.40V#".

PWM full scale ±3600 is confirmed by that document; dead-zone defaults (1000–1900) fit it.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field

import serial  # python3-serial (pyserial 3.5) — already on the BeagleY-AI image

log = logging.getLogger(__name__)

DEFAULT_PORT = "/dev/ttyAMA0"        # header UART pins 8/10 (wiring-manual §5.1b)
FALLBACK_PORT = "/dev/krc-motor"     # USB-C link (udev symlink)
BAUD = 115200
PWM_FULL_SCALE = 3600                # confirmed: $pwm range -3600..3600
INIT_STEP_DELAY_S = 0.1              # Yahboom reference code waits ~100 ms between config commands

PWM_CMD = "pwm"
SPEED_CMD = "spd"


@dataclass
class MotorProfile:
    """One row of the Yahboom profile table (design note §2.4)."""
    mtype: int
    phase: int | None = None          # reduction ratio
    line: int | None = None           # encoder lines
    wheel_dia_mm: float | None = None
    deadzone: int | None = None


# Current drive: doit.am 33GB-520-18.7, 2-wire, no encoder -> open-loop PWM.
# Yahboom's no-encoder profile is type 4; reduction set to our real 18.7 is irrelevant
# without an encoder, so only type + dead zone are sent.
PROFILE_33GB520_NO_ENCODER = MotorProfile(mtype=4, deadzone=1000)
# After the MD520Z30 upgrade (§3.2): matches Yahboom type 1 defaults.
PROFILE_MD520Z30 = MotorProfile(mtype=1, phase=30, line=11, wheel_dia_mm=67.0, deadzone=1900)


@dataclass
class Telemetry:
    total_pulses: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    pulses_10ms: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    speed_mm_s: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    last_rx: float = 0.0
    other: list[str] = field(default_factory=list)   # unrecognised lines, kept for protocol discovery
    battery_v: float | None = None                    # last $Battery reply to $read_vol#


def format_cmd(name: str, *args) -> bytes:
    """Build one ASCII frame: format_cmd('pwm', 1, 2, 3, 4) -> b'$pwm:1,2,3,4#'."""
    body = ",".join(f"{a:.2f}" if isinstance(a, float) else str(int(a)) for a in args)
    return f"${name}:{body}#".encode("ascii")


def parse_frames(buf: bytearray) -> list[str]:
    """Extract complete '$...#' frames from buf in place; returns frame bodies without $/#."""
    frames = []
    while True:
        start = buf.find(b"$")
        if start < 0:
            buf.clear()
            break
        end = buf.find(b"#", start)
        if end < 0:
            del buf[:start]
            break
        frames.append(buf[start + 1:end].decode("ascii", errors="replace").strip())
        del buf[:end + 1]
    return frames


def parse_report(frame: str, telem: Telemetry) -> bool:
    """Apply a $MAll / $MTEP / $MSPD / $Battery frame to telem. Returns False if unrecognised."""
    key, _, rest = frame.partition(":")
    try:
        values = [v.strip() for v in rest.split(",") if v.strip()]
        if key == "MAll":
            telem.total_pulses = [int(v) for v in values][:4]
        elif key == "MTEP":
            telem.pulses_10ms = [int(v) for v in values][:4]
        elif key == "Battery":                       # "Battery:7.40V"
            telem.battery_v = float(rest.strip().rstrip("Vv "))
        elif key == "MSPD":
            telem.speed_mm_s = [float(v) for v in values][:4]
        else:
            return False
    except ValueError:
        return False
    telem.last_rx = time.monotonic()
    return True


class YahboomMotorController:
    """Thread-safe driver. Always call stop() (or use as a context manager) on exit."""

    def __init__(self, port: str = DEFAULT_PORT, max_pwm: int = PWM_FULL_SCALE,
                 pwm_cmd: str = PWM_CMD, speed_cmd: str = SPEED_CMD):
        self.port = port
        self.max_pwm = min(abs(int(max_pwm)), PWM_FULL_SCALE)
        self.pwm_cmd = pwm_cmd
        self.speed_cmd = speed_cmd
        self.telemetry = Telemetry()
        self._ser: serial.Serial | None = None
        self._tx_lock = threading.Lock()
        self._rx_thread: threading.Thread | None = None
        self._running = False

    # -- lifecycle -----------------------------------------------------------
    def open(self) -> None:
        port = self.port
        try:
            self._ser = self._open_port(port)
        except serial.SerialException:
            if port != DEFAULT_PORT:
                raise
            log.warning("%s not found, trying %s", port, FALLBACK_PORT)
            self.port = FALLBACK_PORT
            self._ser = self._open_port(FALLBACK_PORT)
        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, name="yahboom-rx", daemon=True)
        self._rx_thread.start()
        log.info("Opened %s @ %d", self.port, BAUD)

    @staticmethod
    def _open_port(port: str) -> serial.Serial:
        # Hold DTR/RTS low *before* opening: CH340 auto-reset circuits on some boards
        # will otherwise reset the STM32 every time the port opens.
        ser = serial.Serial()
        ser.port = port
        ser.baudrate = BAUD
        ser.timeout = 0.05
        ser.dtr = False
        ser.rts = False
        if os.name == "posix":
            ser.exclusive = True     # flock: GUI and krc-teleop.service can't both drive the board
        ser.open()
        ser.reset_input_buffer()
        return ser

    def close(self) -> None:
        if self._ser is None:
            return
        try:
            self.stop()
        finally:
            self._running = False
            if self._rx_thread:
                self._rx_thread.join(timeout=0.5)
            self._ser.close()
            self._ser = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    # -- commands ------------------------------------------------------------
    def send_raw(self, frame: bytes | str) -> None:
        if isinstance(frame, str):
            frame = frame.encode("ascii")
        if self._ser is None:
            raise RuntimeError("port not open")
        with self._tx_lock:
            self._ser.write(frame)
            self._ser.flush()
        log.debug("TX %s", frame)

    def configure(self, profile: MotorProfile) -> None:
        """Send the Yahboom init sequence (type, phase, line, diameter, deadzone)."""
        steps = [("mtype", profile.mtype), ("mphase", profile.phase), ("mline", profile.line),
                 ("wdiameter", profile.wheel_dia_mm), ("deadzone", profile.deadzone)]
        for name, value in steps:
            if value is None:
                continue
            self.send_raw(format_cmd(name, value))
            time.sleep(INIT_STEP_DELAY_S)

    def set_upload(self, total: bool = False, per_10ms: bool = False, speed: bool = False) -> None:
        self.send_raw(format_cmd("upload", int(total), int(per_10ms), int(speed)))
        time.sleep(INIT_STEP_DELAY_S)

    def set_pwm(self, m1: int = 0, m2: int = 0, m3: int = 0, m4: int = 0) -> None:
        """Open-loop PWM, clamped to ±max_pwm."""
        clamp = lambda v: max(-self.max_pwm, min(self.max_pwm, int(v)))
        self.send_raw(format_cmd(self.pwm_cmd, clamp(m1), clamp(m2), clamp(m3), clamp(m4)))

    def set_speed(self, m1: int = 0, m2: int = 0, m3: int = 0, m4: int = 0) -> None:
        """Closed-loop speed — only meaningful once encoder motors are fitted."""
        self.send_raw(format_cmd(self.speed_cmd, m1, m2, m3, m4))

    def stop(self) -> None:
        if self._ser is not None:
            self.set_pwm(0, 0, 0, 0)

    # -- rx ------------------------------------------------------------------
    def _rx_loop(self) -> None:
        buf = bytearray()
        while self._running and self._ser is not None:
            try:
                chunk = self._ser.read(256)
            except (serial.SerialException, OSError) as exc:
                log.error("serial read failed: %s", exc)
                self._running = False
                break
            if not chunk:
                continue
            buf.extend(chunk)
            for frame in parse_frames(buf):
                log.debug("RX $%s#", frame)
                if not parse_report(frame, self.telemetry):
                    self.telemetry.other = (self.telemetry.other + [frame])[-50:]

    @property
    def link_ok(self) -> bool:
        return self._running
