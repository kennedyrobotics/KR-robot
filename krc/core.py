"""RobotCore — the 50 Hz control loop shared by the desktop app and headless teleop.

Owns the gamepad, the Yahboom motor link and the TeleopController. Runs on its own thread;
front-ends talk to it only through the thread-safe methods below and read `snapshot()`.

Safety rules enforced here (on top of krc/drive.py):
  * motor link loss  -> ESTOP latched, port closed; reconnect is an explicit user action
  * GUI e-stop       -> ESTOP latched; re-arm only from the gamepad (START), never the GUI
  * bench pulse      -> refused while ARMED; cancelled instantly by any e-stop
  * zero PWM is sent on every exit path
"""

from __future__ import annotations

import collections
import json
import logging
import os
import threading
import time
from dataclasses import asdict, dataclass, field

from . import joystick as js
from . import yahboom
from .drive import Inputs, Mode, TeleopConfig, TeleopController

log = logging.getLogger(__name__)

RATE_HZ = 50
SETTINGS_PATH = os.path.expanduser("~/.config/krc-robot/settings.json")


@dataclass
class Settings:
    port: str = yahboom.DEFAULT_PORT
    pwm_cmd: str = yahboom.PWM_CMD
    profile: str = "33gb520"              # 33gb520 | md520
    init_on_connect: bool = True
    auto_connect_motors: bool = True
    teleop: TeleopConfig = field(default_factory=TeleopConfig)

    @classmethod
    def load(cls, path: str = SETTINGS_PATH) -> "Settings":
        s = cls()
        try:
            with open(path) as f:
                raw = json.load(f)
            tele = raw.pop("teleop", {})
            for k, v in raw.items():
                if hasattr(s, k):
                    setattr(s, k, v)
            for k, v in tele.items():
                if hasattr(s.teleop, k):
                    setattr(s.teleop, k, v)
        except (OSError, ValueError):
            pass
        return s

    def save(self, path: str = SETTINGS_PATH) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)


PROFILES = {"33gb520": yahboom.PROFILE_33GB520_NO_ENCODER, "md520": yahboom.PROFILE_MD520Z30}


class RobotCore(threading.Thread):
    def __init__(self, settings: Settings | None = None, gamepad_dev: str | None = None,
                 dry_run: bool = False):
        super().__init__(name="robot-core", daemon=True)
        self.settings = settings or Settings.load()
        self.gamepad_dev = gamepad_dev
        self.dry_run = dry_run
        self.ctl = TeleopController(self.settings.teleop)

        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._pad: js.Gamepad | None = None
        self._motors: yahboom.YahboomMotorController | None = None
        self._next_pad_try = 0.0
        self._bench: tuple[list[int], float] | None = None       # (channels, end time)
        self._pending: collections.deque = collections.deque()     # callables run on the loop thread

        self.events: collections.deque = collections.deque(maxlen=500)   # (seq, wallclock, level, text)
        self._seq = 0
        self.presses = collections.Counter()
        self.pad_connects = self.pad_disconnects = 0
        self.loop_hz = 0.0
        self.last_out = (0, 0)
        self.motor_error = ""

    # -- public API (any thread) --------------------------------------------
    def note(self, text: str, level: str = "info") -> None:
        self._seq += 1
        self.events.append((self._seq, time.time(), level, text))
        log.log({"warn": logging.WARNING, "error": logging.ERROR}.get(level, logging.INFO), text)

    def estop(self, reason: str = "GUI e-stop") -> None:
        with self._lock:
            self._bench = None
            self.ctl.force_estop(reason)
        self.note(f"E-STOP: {reason}", "warn")

    def connect_motors(self) -> None:
        self._pending.append(self._do_connect)

    def disconnect_motors(self) -> None:
        self._pending.append(self._do_disconnect)

    def send_raw(self, frame: str) -> None:
        self._pending.append(lambda: self._motor_call("raw", lambda m: m.send_raw(frame)))

    def set_upload(self, total: bool, per_10ms: bool, speed: bool) -> None:
        self._pending.append(lambda: self._motor_call(
            "upload", lambda m: m.set_upload(total, per_10ms, speed)))

    def init_profile(self) -> None:
        prof = PROFILES[self.settings.profile]
        self._pending.append(lambda: self._motor_call(f"init {self.settings.profile}", lambda m: m.configure(prof)))

    def bench_pulse(self, channel: int, pwm: int, seconds: float) -> str | None:
        """Spin one channel briefly. Returns an error string if refused."""
        with self._lock:
            if self.ctl.mode == Mode.ARMED:
                return "refused: teleop is ARMED (e-stop or disarm first)"
            if self._motors is None and not self.dry_run:
                return "refused: motor board not connected"
            ch = [0, 0, 0, 0]
            ch[channel - 1] = int(pwm)
            self._bench = (ch, time.monotonic() + max(0.1, min(5.0, seconds)))
        self.note(f"bench pulse M{channel} pwm={pwm} for {seconds:.1f}s")
        return None

    def rumble(self, strong: float, weak: float, ms: int = 300) -> str | None:
        with self._lock:
            if self._pad is None:
                return "no gamepad"
            try:
                self._pad.rumble(strong, weak, ms)
            except OSError as e:
                return str(e.strerror or e)
        return None

    def apply_settings(self) -> None:
        """Call after editing self.settings; motor port/keyword changes apply on reconnect."""
        with self._lock:
            self.ctl.cfg = self.settings.teleop
            if self._motors is not None:
                self._motors.max_pwm = min(abs(int(self.settings.teleop.max_pwm)), yahboom.PWM_FULL_SCALE)
                self._motors.pwm_cmd = self.settings.pwm_cmd
        try:
            self.settings.save()
        except OSError as e:
            self.note(f"could not save settings: {e}", "warn")

    def shutdown(self) -> None:
        self._stop.set()
        if self.is_alive():
            self.join(timeout=2.0)

    def snapshot(self) -> dict:
        with self._lock:
            pad = self._pad
            m = self._motors
            return {
                "mode": self.ctl.mode.value,
                "reason": self.ctl.reason,
                "out": self.last_out,
                "max_pwm": self.settings.teleop.max_pwm,
                "loop_hz": self.loop_hz,
                "bench": self._bench is not None,
                "pad": None if pad is None else {
                    "name": pad.name, "path": pad.path,
                    "bus": js.BUS_NAMES.get(pad.bustype, hex(pad.bustype)),
                    "id": f"{pad.vendor:04x}:{pad.product:04x}",
                    "rumble": pad.can_rumble,
                    "axes": dict(pad.state.axes),
                    "one_sided": {c: i.one_sided for c, i in pad.axis_info.items()},
                    "buttons": {c: pad.state.button(c) for c in pad.keys},
                },
                "pad_connects": self.pad_connects,
                "pad_disconnects": self.pad_disconnects,
                "presses": dict(self.presses),
                "motors": None if m is None else {
                    "port": m.port,
                    "telemetry": {
                        "total": list(m.telemetry.total_pulses), "per10ms": list(m.telemetry.pulses_10ms),
                        "speed": list(m.telemetry.speed_mm_s),
                        "age": (time.monotonic() - m.telemetry.last_rx) if m.telemetry.last_rx else None,
                        "other": list(m.telemetry.other[-20:]),
                    },
                },
                "motor_error": self.motor_error,
                "dry_run": self.dry_run,
            }

    # -- loop thread ---------------------------------------------------------
    def run(self) -> None:
        if self.settings.auto_connect_motors and not self.dry_run:
            self._do_connect()
        period = 1.0 / RATE_HZ
        t_prev = time.monotonic()
        ticks, t_rate = 0, t_prev
        t_next = t_prev + period
        try:
            while not self._stop.is_set():
                now = time.monotonic()
                dt, t_prev = now - t_prev, now
                while self._pending:
                    try:
                        self._pending.popleft()()
                    except Exception as e:  # never let a UI action kill the loop
                        self.note(f"action failed: {e}", "error")
                self._tick(now, dt)
                ticks += 1
                if now - t_rate >= 1.0:
                    self.loop_hz, ticks, t_rate = ticks / (now - t_rate), 0, now
                # fixed-deadline scheduling so overhead doesn't accumulate (holds 50 Hz)
                t_next = max(t_next + period, time.monotonic())   # never "catch up" after a stall
                time.sleep(max(0.0, t_next - time.monotonic()))
        finally:
            self._do_disconnect()
            with self._lock:
                if self._pad is not None:
                    self._pad.close()
                    self._pad = None

    def _tick(self, now: float, dt: float) -> None:
        with self._lock:
            # gamepad (re)connect + poll
            if self._pad is None and now >= self._next_pad_try:
                try:
                    self._pad = js.Gamepad(self.gamepad_dev)
                    self.pad_connects += 1
                    self.note(f"gamepad connected: {self._pad.name} ({self._pad.path})")
                except OSError:
                    self._next_pad_try = now + 1.0
            inputs = Inputs(link_ok=False)
            if self._pad is not None:
                try:
                    for etype, code, value in self._pad.poll(0.0):
                        if etype == js.EV_KEY and value == 1:
                            self.presses[code] += 1
                    s = self._pad.state
                    inputs = Inputs(lx=s.axis(js.ABS_X), ly=s.axis(js.ABS_Y), rx=s.axis(js.ABS_RX),
                                    ry=s.axis(js.ABS_RY), deadman=s.button(js.BTN_TL),
                                    arm=s.button(js.BTN_START),
                                    estop=s.button(js.BTN_EAST) or s.button(js.BTN_MODE))
                except OSError as e:
                    self.pad_disconnects += 1
                    self.note(f"gamepad link lost ({e.strerror or e}) — SAFE STOP", "warn")
                    self._pad.close()
                    self._pad = None
                    self._next_pad_try = now + 1.0

            prev_mode = self.ctl.mode
            left, right = self.ctl.update(inputs, dt)
            if self.ctl.mode != prev_mode:
                self.note(f"{prev_mode.value} -> {self.ctl.mode.value} ({self.ctl.reason})",
                          "warn" if self.ctl.mode == Mode.ESTOP else "info")
                if self.ctl.mode == Mode.ESTOP or (self.ctl.mode != Mode.ARMED and self._bench):
                    self._bench = None
                if self._pad is not None and self._pad.can_rumble:   # haptic confirmation
                    try:
                        self._pad.rumble(*((0.0, 0.6) if self.ctl.mode == Mode.ARMED else (1.0, 0.0)), 250)
                    except OSError:
                        pass
            channels = self.ctl.channels(left, right)

            if self._bench is not None:
                if self.ctl.mode == Mode.ARMED or now >= self._bench[1]:
                    self._bench = None
                    self.note("bench pulse finished")
                else:
                    channels = list(self._bench[0])
                    left, right = channels[self.settings.teleop.left_channel - 1], \
                        channels[self.settings.teleop.right_channel - 1]
            self.last_out = (left, right)

            if self._motors is not None:
                if not self._motors.link_ok:
                    self._motor_lost("serial read failed")
                else:
                    try:
                        self._motors.set_pwm(*channels)
                    except Exception as e:
                        self._motor_lost(str(e))

    def _motor_lost(self, why: str) -> None:
        self.motor_error = why
        self.note(f"motor link lost: {why} — E-STOP", "error")
        self.ctl.force_estop("motor link lost")
        self._bench = None
        try:
            self._motors.close()
        except Exception:
            pass
        self._motors = None

    def _motor_call(self, what: str, fn) -> None:
        with self._lock:
            if self._motors is None:
                self.note(f"{what}: motor board not connected", "warn")
                return
            try:
                fn(self._motors)
                self.note(f"{what}: sent")
            except Exception as e:
                self._motor_lost(f"{what}: {e}")

    def _do_connect(self) -> None:
        with self._lock:
            if self._motors is not None:
                return
            s = self.settings
            m = yahboom.YahboomMotorController(s.port, max_pwm=s.teleop.max_pwm, pwm_cmd=s.pwm_cmd)
            try:
                m.open()
                if s.init_on_connect:
                    m.configure(PROFILES[s.profile])
                m.stop()
            except Exception as e:
                self.motor_error = str(e)
                self.note(f"motor board connect failed: {e}", "warn")
                try:
                    m.close()
                except Exception:
                    pass
                return
            self._motors = m
            self.motor_error = ""
            self.note(f"motor board connected on {m.port}")

    def _do_disconnect(self) -> None:
        with self._lock:
            if self._motors is None:
                return
            try:
                self._motors.close()       # sends zero PWM first
            finally:
                self._motors = None
                self.note("motor board disconnected")
