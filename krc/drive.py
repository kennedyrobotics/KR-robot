"""Skid-steer mixing and teleop safety state machine — pure logic, no I/O.

Safety model (design note §6.1/§6.2):
  * DISARMED at start-up and after any link loss. Motors held at zero.
  * ARM:     press START while the deadman (LB) is *released* and sticks are centred.
  * DRIVE:   motion only while the deadman (LB) is held. Release -> immediate zero.
  * E-STOP:  B (or HOME) latches ESTOP from any state. Stopping is easy; re-arming is
             deliberate: release everything, then press START.
  * LINK LOSS (gamepad ENODEV / read error, motor serial error): -> DISARMED, zero output.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum


class Mode(Enum):
    DISARMED = "DISARMED"
    ARMED = "ARMED"
    ESTOP = "ESTOP"


def deadband(v: float, band: float) -> float:
    """Zero inside +-band, rescale the rest back to full 0..1 so there is no step."""
    if abs(v) <= band:
        return 0.0
    return math.copysign((abs(v) - band) / (1.0 - band), v)


def expo(v: float, k: float) -> float:
    """k=0 linear, k=1 fully cubic. Softer centre for fine positioning."""
    return (1.0 - k) * v + k * v ** 3


def arcade_mix(throttle: float, steer: float) -> tuple[float, float]:
    """throttle/steer in -1..1 -> (left, right) in -1..1, normalised so neither saturates."""
    left = throttle + steer
    right = throttle - steer
    m = max(1.0, abs(left), abs(right))
    return left / m, right / m


@dataclass
class TeleopConfig:
    max_pwm: int = 1800            # bench default ~50 % of the (unverified) 3600 full scale
    stick_deadband: float = 0.08
    expo: float = 0.3
    turn_scale: float = 0.6        # pivot turns load the drivers hard (§3.1) — limit them
    slew_per_s: float = 3.0        # max change in normalised output per second
    tank: bool = False
    invert_left: bool = False
    invert_right: bool = True      # mirrored motor mounting is typical; confirm on the bench
    left_channel: int = 1          # M1 = left track
    right_channel: int = 2         # M2 = right track


@dataclass
class Inputs:
    lx: float = 0.0
    ly: float = 0.0                # evdev: stick up is NEGATIVE
    rx: float = 0.0
    ry: float = 0.0
    deadman: bool = False          # LB
    arm: bool = False              # START
    estop: bool = False            # B or HOME
    link_ok: bool = True


class TeleopController:
    def __init__(self, cfg: TeleopConfig):
        self.cfg = cfg
        self.mode = Mode.DISARMED
        self.reason = "start-up"
        self._left = 0.0
        self._right = 0.0
        self._prev_arm = False

    def force_estop(self, reason: str) -> None:
        """Latch ESTOP from outside the gamepad (GUI button, motor link loss). Re-arm with START."""
        self.mode, self.reason = Mode.ESTOP, reason
        self._left = self._right = 0.0

    def _sticks_centred(self, i: Inputs) -> bool:
        b = self.cfg.stick_deadband
        return all(abs(v) <= b for v in (i.lx, i.ly, i.rx, i.ry))

    def update(self, i: Inputs, dt: float) -> tuple[int, int]:
        """Advance one control tick. Returns (left_pwm, right_pwm) after inversion."""
        arm_edge = i.arm and not self._prev_arm
        self._prev_arm = i.arm

        if not i.link_ok:
            # ESTOP is stricter than DISARMED, so it stays latched through link loss
            if self.mode == Mode.ARMED:
                self.mode, self.reason = Mode.DISARMED, "link lost"
        elif i.estop:
            self.mode, self.reason = Mode.ESTOP, "e-stop pressed"
        elif arm_edge and self.mode != Mode.ARMED:
            if i.deadman or not self._sticks_centred(i):
                self.reason = "arm refused: release LB and centre sticks"
            else:
                self.mode, self.reason = Mode.ARMED, "armed"

        target_l = target_r = 0.0
        if self.mode == Mode.ARMED and i.deadman:
            c = self.cfg
            if c.tank:
                target_l = expo(deadband(-i.ly, c.stick_deadband), c.expo)
                target_r = expo(deadband(-i.ry, c.stick_deadband), c.expo)
            else:
                thr = expo(deadband(-i.ly, c.stick_deadband), c.expo)
                steer = expo(deadband(i.rx, c.stick_deadband), c.expo) * c.turn_scale
                target_l, target_r = arcade_mix(thr, steer)

        if target_l == 0.0 and target_r == 0.0:
            # stopping is never rate-limited
            self._left = self._right = 0.0
        else:
            step = self.cfg.slew_per_s * dt
            self._left += max(-step, min(step, target_l - self._left))
            self._right += max(-step, min(step, target_r - self._right))

        left = -self._left if self.cfg.invert_left else self._left
        right = -self._right if self.cfg.invert_right else self._right
        return round(left * self.cfg.max_pwm), round(right * self.cfg.max_pwm)

    def channels(self, left_pwm: int, right_pwm: int) -> list[int]:
        out = [0, 0, 0, 0]
        out[self.cfg.left_channel - 1] = left_pwm
        out[self.cfg.right_channel - 1] = right_pwm
        return out
