# KR-Robot — Motor Control and Joystick

Hardware notes and integration decisions for the KR-bot tracked robot: drive train, motor controller, battery, and operator joystick. Feeds Phase 0–1 of the 5-layer architecture (L1 `YahboomMotorControllerDriver`, L5 `InputDriver` / watchdog).

Last updated: 2026-10-01

---

## 1. System summary

| Item | Part | Notes |
|---|---|---|
| Compute | BeagleY-AI | Linux host, runs L1–L5 |
| Motor controller | Yahboom 4-Channel Encoder Motor Drive Module (YB-ESF01-V2.0) | STM32 co-processor, AT8236 H-bridges, CH340K USB-serial, DC 5–12 V input |
| Drive motors (current) | doit.am 33GB-520-18.7, 12 V, 350 RPM ±10% | 2 off (one per track), **no encoder** (2-wire) |
| Chassis | Gold aluminium tracked chassis | Skid-steer, one motor per track |
| Battery | NXE Power Super-Nano 2S LiPo, 7.4 V, 5400 mAh, 50C | Deans/T-plug main lead, JST-XH balance lead |
| Joystick | SN2403 game controller | Wired / Bluetooth / 2.4 GHz receiver (receiver not supplied) |

---

## 2. Motor controller — Yahboom 4-Channel Encoder Motor Drive Module

### 2.1 Board identification
- Silkscreen: YB-ESF01-V2.0, STM32F103RCT6 MCU, AT8236 motor drivers ×4, CH340K USB-UART, AMS1117-3.3 regulator.
- Runs its own closed-loop speed control on board when encoder motors are fitted — the host sends speed (or PWM) commands, not raw H-bridge PWM.

### 2.2 Connectors
| Connector | Pins | Use |
|---|---|---|
| Motor 1–4 (6-pin PH2.0) | Mx+, Mx−, GND, 3V3, HxA, HxB | Encoder motors (motor + hall encoder) |
| Motor 1–4 (2-pin) | Mx+, Mx− | Plain DC motors (no encoder) |
| I2C (4-pin) | 5V, GND, SCL, SDA | Host link option A |
| UART (4-pin) | 5V, GND, TX2, RX2 | Host link option B |
| USB-C | CH340K | Host link option C (USB serial) |
| Screw terminal | + / − | Power input DC 5–12 V |

Board card warning: use official Yahboom encoder motors, otherwise the drive board may be damaged. This concerns the 6-pin encoder header pinout/voltage; plain 2-wire motors go on the 2-pin ports.

### 2.3 Host interface — decision: USB-C serial
- I2C and serial **cannot be used at the same time** — only one interface can be selected.
- **Chosen: USB-C → BeagleY-AI USB.** Appears as `/dev/ttyUSB0` (check `ttyUSB1` if not). Avoids 5 V vs 3.3 V logic-level risk and I2C pull-up uncertainty.
- If I2C is ever used instead: with the board powered and disconnected, measure SDA/SCL idle voltage before connecting. BeagleY-AI GPIO is **3.3 V only**; the header is labelled 5V.

### 2.4 Protocol — known so far (from Yahboom reference code)
Init sequence (each command followed by ~100 ms delay):
1. Motor type
2. Reduction ratio
3. Encoder magnetic-ring line count
4. Wheel diameter (mm)
5. Motor dead zone

Yahboom profiles:

| Type | Motor | Reduction | Encoder lines | Wheel dia (mm) | Dead zone |
|---|---|---|---|---|---|
| 1 | 520 motor | 30 | 11 | 67.00 | 1900 |
| 2 | 310 motor | 20 | 13 | 48.00 | 1600 |
| 3 | TT with speed disc | 45 | 13 | 68.00 | 1250 |
| 4 | TT DC (no encoder) | 48 | — | — | 1000 |
| 5 | L-type 520 | 40 | 11 | 67.00 | 1900 (sent as type 1) |

Control modes:
- **Speed control** (closed loop) — encoder motors only.
- **PWM control** (open loop) — motors without encoders (Yahboom uses this for type 4).

Encoder readback:
- Total pulse count — 32-bit, big-endian, read as high and low 16-bit halves.
- Real-time pulses per 10 ms — 16-bit, big-endian, signed.

Serial example (from Yahboom motor docs): `$mtype:1#` selects the 520 motor type.

**Command set (resolved 2026-10-09).** Source: Yahboom "1.2 Control command.pdf" in https://github.com/YahboomTechnology/4-Channel-Motor-Drive-Module (`1.Introduction/`). Commands are case-insensitive; config commands reply `<command>OK`.

| Serial command | Meaning | Notes |
|---|---|---|
| `$mtype:x#` | Motor type: 1 = 520, 2 = 310, 3 = TT with encoder, **4 = TT without encoder** (ours) | Saved to flash |
| `$deadzone:x#` | PWM dead zone, 0–3600 (default 1600) | Saved |
| `$mline:x#`, `$mphase:x#`, `$wdiameter:x#` | Encoder lines, gear ratio, wheel diameter (mm) | Encoder motors only; saved |
| `$MPID:p,i,d#` | Speed PID (default 0.8, 0.06, 0.5); the chip restarts | Encoder motors only; saved |
| `$flash_reset#` | Factory defaults; the chip restarts | |
| `$spd:m1,m2,m3,m4#` | Closed-loop speed, −1000…1000 | Encoder motors only; no reply |
| `$pwm:m1,m2,m3,m4#` | **Open-loop PWM, −3600…3600** (confirms the full scale) | No reply |
| `$upload:a,b,c#` | Stream `$MAll` (total pulses), `$MTEP` (pulses/10 ms), `$MSPD` (mm/s) | Encoder motors only |
| `$read_flash#` | Stored settings | No reply seen on our board (2 s wait) |
| `$read_vol#` | **Battery voltage → `$Battery:7.40V#`** (0.1 V steps) | **Verified 2026-10-09: `$Battery:6.7V#`** |

I2C (address `0x26`, only if the serial link is not used): `0x01`–`0x07` mirror the config/`spd`/`pwm` commands; `0x08` reads battery (`(buf[0]<<8|buf[1])/10.0` V); `0x10`–`0x13` 10 ms pulses M1–M4; `0x20`–`0x27` total pulses.

**Not available from the board:** motor current, temperature, fault flags, firmware version. Current needs extra hardware: the proposed INA226 on the battery lead (wiring manual IF-07) for total current, or a sensor per motor lead.

krbot polls `$read_vol#` every second (`motor.battery_poll_ms`). The monitors show it as a Battery card, and any reply from the board counts as its heartbeat ("board: replying").

---

## 3. Drive motors

### 3.1 Current motors — 33GB-520-18.7
- 12 V rated, 350 RPM ±10%, 18.7:1 gearbox, 2-wire, **no encoder**.
- Connect to **2-pin M1 (left track) and M2 (right track)** ports, not the 6-pin encoder headers.
- Currently the two motor leads are joined via yellow XT30 plugs — separate them so each motor has its own channel.
- Drive in **PWM (open-loop) mode**. No wheel odometry available.
- `IMotorController::setChannelSpeed()` maps to an open-loop PWM command for now.
- Skid-steer pivot turns load motors hard — monitor driver IC temperature during long pivots.

### 3.2 Recommended encoder upgrade
**Part:** Yahboom MD520Z30_12V — 520 DC gear motor with hall encoder, 333 RPM, 1:30, 11-line encoder.
- Matches board motor type 1 default profile — no parameter tuning.
- 333 RPM is closest to the current 350 RPM, so track speed is preserved.
- Alternatives in same family: 205 RPM (1:56) for more torque, 550 RPM (1:19) for more speed.
- Quantity: 2 (one per track). Order with the PH2.0 6-pin cable for direct connection to the board.

**Suppliers:**
- Yahboom direct: https://category.yahboom.net/products/md520
- RobotShop (motor + bracket): https://www.robotshop.com/products/yahboom-yahboom-520-dc-gear-motor-with-encoder-333rpm130-motorbracket
- RobotShop (motor only): https://www.robotshop.com/products/yahboom-yahboom-520-dc-gear-motor-with-encoder-333rpm130-separate-motor

**Generic alternative:** JGB37-520 with 11-line hall encoder — set reduction ratio and line count in board config.

**Check before ordering:**
1. **Mechanical fit** — chassis brackets are for the doit.am 33GB-520. Compare gearbox diameter, face screw pattern, and output D-shaft diameter/length (track sprocket fit). "520" refers only to the motor can.
2. **Supply voltage** — see §4.2.

---

## 4. Power

### 4.1 Battery — NXE 2S LiPo 7.4 V 5400 mAh 50C
- Voltage range 6.0–8.4 V — within board DC 5–12 V input.
- Current 12 V motors run at roughly 60–70% speed and reduced torque on 2S. Acceptable for teleop.
- **No low-voltage cutoff on the board.** Minimum ~6.6 V (3.3 V/cell). Fit a LiPo alarm on the balance lead. **Software monitoring is in (2026-10-09):** krbot reads the board's `$read_vol#` every second, warns below `battery.warn_v` (7.0 V) and `battery.low_v` (6.6 V), and refuses to ARM on a fresh reading below `battery.block_arm_below_v` (6.6 V). It never stops a robot that is already driving. Readings are 0.1 V steps and sag under load.
- **50C pack = very high short-circuit current.** Fit an inline fuse (~10 A) and a main power switch between battery and board.
- Battery has Deans/T-plug; board has screw terminal — make a T-plug pigtail.

### 4.2 Voltage for 520 encoder motors
- Yahboom recommends **12 V** for 520 motors (11–16 V range); board listing pairs 7.4 V with 310/TT motors only and warns the wrong voltage can cause power problems.
- 2S is marginal for closed-loop control with 520 encoder motors.
- 3S LiPo = 12.6 V fully charged — slightly above the board's "5–12 V" label. **Confirm the real input limit with Yahboom support** (support@yahboom.com) or use Yahboom's 12 V pack before moving to 3S.

### 4.3 BeagleY-AI power
- Do **not** power the BeagleY-AI from the motor board's 5 V pin.
- Use a separate 5 V buck converter (≥5 A) from the battery; BeagleY-AI needs a solid 5 V / 3 A+. Keeps motor noise off the compute rail and avoids brownouts.

---

## 5. Joystick — SN2403 game controller

### 5.1 Capabilities (from manual)
- Connection: wired USB, Bluetooth, 2.4 GHz wireless (receiver).
- Modes: P4, PC, Switch, Receiver, Android, iOS.
- PC mode: wired defaults to **XINPUT**; long press Windows + Menu for 3 s to switch to DINPUT.
- PC / iOS Bluetooth: advertises as **"Xbox Wireless Controller"**.
- Menu: long press M for 2 s; D-pad to move, A confirm, B return.
- Auto shutdown in wireless mode: 5 min no input, low battery, or no reconnect within 5 min.
- Triggers: L2/R2 levers on back — long travel = analog, short travel = digital.
- Button test menu available for checking key values.

### 5.2 2.4 GHz receiver
- Manual section 6 "Receiver Connection" describes a receiver with its own pairing button.
- **Receiver not supplied.** It is a separate accessory — contact seller. Generic 2.4 GHz dongles will not work (proprietary pairing).

### 5.3 Connection options (in order of preference)
1. **Bluetooth, PC mode, onboard BeagleY-AI radio** — try first. Onboard radio is believed BLE-only; works only if the pad's Xbox emulation uses BLE.
2. **USB Bluetooth Classic dongle on BeagleY-AI** — if option 1 doesn't appear in scan. Pad in PS4 or Switch mode → `hid-playstation` / `hid-nintendo`.
3. **8BitDo USB Wireless Adapter 2** — presents pad to Linux as wired Xbox controller (`xpad`). Third-party pad compatibility not guaranteed.
4. **Wired USB, XInput** — for bench testing and `InputDriver` development now. Binds to `xpad`.

### 5.4 Bluetooth pairing procedure (option 1)
On the pad: hold M 2 s → Mode Switch → PC → A → Pair/Reconnect.

On the BeagleY-AI:
```
bluetoothctl
power on
scan on
# look for "Xbox Wireless Controller"
pair <MAC>
trust <MAC>
connect <MAC>
```
Verify with `evtest`. Install `xpadneo` for cleaner Xbox-over-Bluetooth mapping.

### 5.5 Wired verification
```
lsusb
dmesg | tail
sudo evtest      # select new /dev/input/eventX, move sticks
```

---

## 6. Software integration notes

### 6.1 `InputDriver` (L5)
- Read `/dev/input/eventX` via `libevdev`.
- Left stick → throttle / steering (or tank-style: left stick Y = left track, right stick Y = right track).
- E-stop on a button that is hard to press accidentally.
- Manual drive and e-stop take the direct L5 → L1 path, bypassing L3/L4 and the goal arbiter.

### 6.2 Watchdog / safe-stop
- Treat any `libevdev` read error or `ENODEV` (dongle unplugged, pad asleep, BT drop) as **link loss → direct safe-stop to L1**.
- The pad's 5-minute auto-sleep will trigger this on a parked robot — expected; press HOME to reconnect.

### 6.3 `YahboomMotorControllerDriver` (L1)
- Transport: USB serial (`/dev/ttyUSB0`), not I2C — update design doc §2 / §8 which currently assume raw `ioctl` I2C via `I2CDevice`.
- Current motors: PWM mode, open loop, no odometry.
- After encoder upgrade: speed mode, motor type 1, encoder readback feeds L2 odometry.

---

## 7. Open items

- [x] Obtain Yahboom full serial command set / I2C register map (2026-10-09, §2: Yahboom GitHub "1.2 Control command")
- [ ] Contact seller re SN2403 2.4 GHz receiver.
- [ ] Test SN2403 Bluetooth pairing with onboard BeagleY-AI radio (PC mode).
- [ ] Build T-plug pigtail, inline ~10 A fuse, and main power switch.
- [ ] Fit LiPo low-voltage alarm.
- [ ] Source 5 V ≥5 A buck converter for BeagleY-AI.
- [ ] Separate motor leads; wire to M1 / M2 2-pin ports.
- [ ] Measure 33GB-520 mounting (gearbox dia, screw pattern, shaft) vs Yahboom MD520 drawing.
- [ ] Confirm board max input voltage with Yahboom before any move to 3S.
- [ ] Update architecture doc for USB-serial motor transport.

---

## Sources
- Yahboom — 4 channel encoder motor driver module (USART): https://www.yahboom.net/public/upload/upload-html/1740817239/4%20channel%20encoder%20motor%20driver%20module-USART.html
- Yahboom — Drive motor and read encoder (IIC): https://www.yahboom.net/public/upload/upload-html/1740656355/Drive%20motor%20and%20read%20encoder-IIC.html
- Yahboom — Motor introduction and usage: https://www.yahboom.net/public/upload/upload-html/1740736196/0.%20Motor%20introduction%20and%20usage.html
- Yahboom — 520 motor product page: https://category.yahboom.net/products/md520
- Yahboom 4-channel driver module listing (Amazon): https://www.amazon.com/Yahboom-Co-Processor-RaspberryPi-Robotics-Projects/dp/B0DYXWVB8H
- SN2403 Game Controller User Manual (printed)
