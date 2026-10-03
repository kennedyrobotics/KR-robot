# Bring-up guide: motor board, joystick, krbot

How to get the robot running on the bench, from a fresh deploy to live teleop. It covers two toolsets, which share the same protocol framing and safety state machine:

- **The Python bench tools** ([krc/](../krc/), [tools/](../tools/), and the KR-Robot Control app). Use these for protocol discovery and hardware checks.
- **The production C++ stack, `krbot`** ([krbot/](../krbot/)). It runs as a background service and is watched with the KR-bot Monitor app.

See [../README.md](../README.md) for the architecture and [system-design.md](system-design.md) for how the C++ stack maps onto Design doc v3. Wiring, pin allocation and the per-interface ICD are in [wiring-manual.md](wiring-manual.md).

> **Only one program can drive the motor board at a time.** The serial port is opened exclusively with `flock`, so stop `krbot` before you use the Python tools, and close the Python tools before you start `krbot`. The second one to start gets "in use".

## 1. Toolset

| Path | What it is |
|---|---|
| `krc/yahboom.py` | Python motor-board driver: `$cmd:args#` framing, init sequence, PWM/speed, `$MAll`/`$MTEP`/`$MSPD` telemetry |
| `krc/joystick.py` | Python gamepad reader using raw evdev `ioctl`s. It auto-detects the pad, normalises the axes, supports rumble, and reports link loss as `ENODEV` |
| `krc/drive.py` | Skid-steer mixing plus the arm / deadman / e-stop / link-loss state machine (pure logic) |
| `tools/joystick-controller-debug.py` | Full-screen verification dashboard: every axis and button, stick plots, rumble, teleop preview, link health, and an exit report |
| `tools/yahboom_probe.py` | `listen`, `raw`, `init`, `pwm` and `stop` modes, for discovering and verifying the protocol |
| `tools/joy_test.py` | `--list` shows the axis ranges. The default view shows live sticks and buttons |
| `tools/teleop.py` | Headless Python teleop. `--dry-run` sends no motor output |
| `krc_robot_gui.py` | **KR-Robot Control** desktop app: Python teleop, bench pulse, and protocol tools |
| `krbot/` + `scripts/build-krbot.sh` | **The C++ stack.** Builds and tests it natively on the board |
| `krbot_monitor_gui.py` | **KR-bot Monitor** desktop app: live status, events, E-STOP, and service control for `krbot` |
| `scripts/krbot-console.sh` | Terminal view of `krbot` for SSH sessions |
| `scripts/monitor-from-pc.ps1` | Runs KR-bot Monitor on the Windows PC through an SSH tunnel |
| `scripts/krc-diag.sh` | Reports what's connected and what's missing. Needs no sudo |
| `scripts/bt-pair-gamepad.sh` | BLE scan, then pair, trust and connect the gamepad |
| `scripts/sudo-setup.sh` | One-time root setup: udev `/dev/krc-motor`, groups, packages, systemd units, and optionally `--bluetooth` |
| `tests/`, `krbot/tests/` | Hardware-free unit tests. Python runs on Windows or the board; C++ runs on the board |

## 2. Deploy and one-time setup

From Windows, in the VS Code terminal:

```powershell
cd Software\code
.\deploy.ps1                 # -> beagle@192.168.1.116:~/krc-robot  (also installs desktop icons + krbot user unit)
```

On the board, once, in an interactive session so that sudo can prompt for the password:

```bash
bash ~/krc-robot/scripts/sudo-setup.sh --bluetooth
# installs: evtest, python3-serial, python3-tk, udev rule, groups, krc-teleop.service (not enabled),
#           bluetooth.service. Gamepad BT is the CSR8510 USB dongle; onboard CC3301 BLE stays OFF
#           (krc-ble-enable.service crashes the boot alongside the dongle, notes/bluetooth-debugging.md §7)
# then pair the pad: bash ~/krc-robot/scripts/bt-pair-gamepad.sh
# log out/in so the dialout + input groups take effect
bash ~/krc-robot/scripts/build-krbot.sh          # C++ stack: cmake + ninja + ctest (43 tests)
```

The C++ build needs `libgtest-dev`, `ninja-build` and `libsqlite3-dev` from apt. These are already installed on the current board.

## 3. Bench procedure

> **Lift the chassis on blocks so both tracks are off the ground before any motor command.**
> Fit the inline fuse and main switch first (design note §4.1).

1. **Diagnostics.** Run `bash ~/krc-robot/scripts/krc-diag.sh`. Expect the motor serial port, the gamepad and Bluetooth (`hci0`) to all report ready.
2. **Gamepad, wired, PC mode (XInput).** Plug it into a **USB-A** port with a **data** cable. The USB-C socket is power input only, and charge-only cables don't work. Then run `~/krc-robot/tools/joystick-controller-debug.py --report ~/joystick-report.txt` from a terminal on the board, or over `ssh -t`.
   - Move every stick and trigger to its end stops and press every button until `VERIFIED n/n` turns green.
   - Check that `rest drift` is green with the sticks released.
   - Press `r` and `w` to test the rumble motors.
   - Try START, LB and B, and confirm that the teleop preview arms, drives and e-stops as expected.
   - Unplug the pad. Check that `disconnects` increments and the view shows LINK LOST, then replug it.
   - `--info` gives a non-interactive capability dump.
3. **Gamepad over Bluetooth (optional).**
   - Unplug the cable.
   - On the pad: hold M for 2 s, then Mode Switch → PC → A → Pair/Reconnect.
   - Run `ssh -t beagle@192.168.1.116 bash ~/krc-robot/scripts/bt-pair-gamepad.sh`.

   The onboard radio is **BLE only**. If the pad never appears in the scan, its PC mode uses Classic Bluetooth, and you need to fall back to a dongle. See [../notes/bluetooth-debugging.md](../notes/bluetooth-debugging.md).
4. **Motor board protocol (Phase 0)**, with `krbot` stopped.
   - `yahboom_probe.py -v listen -t 5` captures any boot banner.
   - `yahboom_probe.py -v raw '$upload:1,0,0#' -t 3` should start `$MAll` frames. Stop them with `raw '$upload:0,0,0#'`.
   - `yahboom_probe.py init` sends `$mtype:4#` and `$deadzone:1000#`.
5. **One motor at a time.** Run `yahboom_probe.py pwm 1 600 -d 1`, then raise the value gradually. Record:
   - the PWM value at which the track first moves (the real dead zone)
   - whether `$pwm:` is accepted. If not, try `--pwm-cmd` with another keyword, or use `raw`. Then set `motor.pwm_keyword` in `krbot/config/krbot.conf` to match.
   - the direction of each track at positive PWM. This sets the invert options (`teleop.invert_left` / `teleop.invert_right`).
6. **Dry-run teleop.**
   - **C++:** open **KR-bot Monitor**, go to the Service tab and press **Start DRY RUN**. That gives a simulated motor board with the real gamepad.
   - **Python:** run `teleop.py --dry-run` instead.

   Then:
   - Press START to arm, hold LB and move the sticks. Check the signs of L and R on the track bars.
   - Test that B latches ESTOP, and that **SPACE** in the monitor does too.
   - Unplug the pad and confirm the robot disarms.
7. **Live teleop, still on blocks.** In KR-bot Monitor, go to Service and press **Start**. Keep `teleop.max_output` at or below 1200 in `krbot.conf`, and raise it only once step 5 has confirmed the full scale. The Python equivalent is `teleop.py --max-pwm 1200`.
8. **After bench sign-off:** press **Boot: on** in the monitor's Service tab, or run `systemctl --user enable krbot`. Also run `loginctl enable-linger` (no sudo needed) so it starts at boot without waiting for a login. It always comes up DISARMED. **Done on this robot 2026-10-03, verified across a reboot.**

### Teleop controls

| Input | Action |
|---|---|
| START | Arm. LB must be released and the sticks centred |
| hold LB | Deadman: the robot moves only while it is held |
| Left stick Y / right stick X | Throttle / steer (tank mode: left Y / right Y) |
| B or HOME | E-stop, latched. Re-arm with START |
| SPACE / ESC / red button in either desktop app | E-stop, latched. Re-arm only from the gamepad |
| `systemctl --user kill -s USR1 krbot` | E-stop for `krbot` from a shell |
| Pad lost, BT drop, pad sleep | Disarm and zero output. Reconnects automatically but stays disarmed |
| Motor board fault | ESTOP is held every tick. START cannot arm until the board is healthy again |

## 4. Running `krbot`

| Task | How |
|---|---|
| Start / stop / restart | KR-bot Monitor → Service tab, or `systemctl --user start\|stop\|restart krbot` |
| Dry run (simulated motors) | Monitor → **Start DRY RUN**, or `systemctl --user set-environment KRBOT_ARGS=--dry-run` and then restart |
| Watch it | **KR-bot Monitor** desktop icon. Over SSH: `bash ~/krc-robot/scripts/krbot-console.sh`, or `journalctl --user -u krbot -f` |
| Watch it from the PC | `.\scripts\monitor-from-pc.ps1` in `Software\code`, which goes through an SSH tunnel |
| After a deploy | Rebuild (Monitor → **Rebuild krbot**, or run `build-krbot.sh`), then **Restart** |
| Run it in the foreground | Stop the service, then run `~/krc-robot/krbot/build/krbot [--dry-run] [--log=debug] [--section.key=value]` |

## 5. Verified and still unverified

**Verified on the board:**

- **SN2403 wired:** enumerates as `045e:028e` through `xpad`, with rumble working.
- **SN2403 over Bluetooth (2026-10-03):** Classic BT via the CSR8510 USB dongle. Reconnects by itself after power loss and reboot. Arm (START), deadman (LB), e-stop (HOME/B) and track response checked on blocks.
- **`krbot` at boot (2026-10-03):** starts by itself after a reboot, DISARMED, and picks up the pad.
- **Onboard BLE adapter:** works via `krc-ble-enable.service`, but is now **disabled**: with the USB dongle present at boot, `btti_uart` oopses and wedges the boot.
- **`krbot` without the motor board:** the dry run holds 50.0 Hz. In real mode with no board attached, it latches ESTOP.
- **E-STOP from the monitor and via `SIGUSR1`:** both latch.

**Still unverified:**

- The exact `$pwm:` and `$spd:` keywords, and the PWM full scale (assumed ±3600).
- Whether the board has its own command timeout. Teleop re-sends at 50 Hz either way.
- The CH340K USB ID. The udev rule covers both `1a86:7522` and `1a86:7523`.
- Whether the SN2403 pairs over BLE, which is the pending step 3 test.
- Live teleop with the motor board (steps 5–7).
