# Bring-up guide: motor board + joystick

Bench tools for Phase 0–1. They bring up the Yahboom 4-channel motor board (USB serial) and the SN2403 gamepad on the BeagleY-AI. Python 3 only, no third-party packages beyond `python3-serial`, which ships with the BeagleY-AI image. These modules are the reference implementations for the C++ L1 `YahboomMotorControllerDriver` and L5 `InputDriver`. See [../README.md](../README.md) for the architecture.

| Path | What it is |
|---|---|
| `krc/yahboom.py` | Motor board driver: `$cmd:args#` framing, init sequence, PWM/speed, `$MAll`/`$MTEP`/`$MSPD` telemetry |
| `krc/joystick.py` | Gamepad reader using raw evdev `ioctl`s. Auto-detects the pad, normalises axes, reports link loss as `ENODEV` |
| `krc/drive.py` | Skid-steer mixing plus the arm / deadman / e-stop / link-loss state machine (pure logic) |
| `tools/joystick-controller-debug.py` | Full-screen verification dashboard: every axis and button, stick plots, rumble, teleop preview, link health, exit report |
| `tools/yahboom_probe.py` | `listen`, `raw`, `init`, `pwm`, `stop`. Use it to discover and verify the protocol |
| `tools/joy_test.py` | `--list` shows axis ranges; the default view is live sticks and buttons |
| `tools/teleop.py` | Drives the robot with the joystick. `--dry-run` sends no motor output |
| `scripts/krc-diag.sh` | Reports what's connected and what's missing (no sudo) |
| `scripts/sudo-setup.sh` | One-time root setup: udev `/dev/krc-motor`, groups, evtest, systemd unit, optional `--bluetooth` |
| `tests/` | Hardware-free unit tests: `python3 -m unittest discover -s tests -v` |

## Deploy (from Windows, VS Code terminal)

```powershell
cd Software\code
.\deploy.ps1                 # -> beagle@192.168.1.116:~/krc-robot
```

On the board, once and interactively, so that sudo can prompt for the password:

```bash
bash ~/krc-robot/scripts/sudo-setup.sh --bluetooth
# log out/in so the dialout + input groups take effect
```

## Bench procedure

> **Lift the chassis on blocks so both tracks are off the ground before any motor command.**
> Fit the inline fuse and main switch first (design note §4.1).

1. **Diagnostics.** Run `bash ~/krc-robot/scripts/krc-diag.sh`. Expect motor serial ready and gamepad ready.
2. **Gamepad (wired, XInput).** From a terminal on the board, or over `ssh -t`, run `~/krc-robot/tools/joystick-controller-debug.py --report ~/joystick-report.txt`.
   - Move every stick and trigger to its end stops and press every button until `VERIFIED n/n` turns green.
   - Check that `rest drift` is green with the sticks released.
   - Press `r` and `w` to test the rumble motors.
   - Try START, LB and B and confirm the teleop preview arms, drives and e-stops as expected.
   - Unplug the pad and check that `disconnects` increments and the view shows LINK LOST, then replug it.
   - `--info` gives a non-interactive capability dump.
3. **Motor board protocol (Phase 0).**
   - `yahboom_probe.py -v listen -t 5` captures any boot banner.
   - `yahboom_probe.py -v raw '$upload:1,0,0#' -t 3` should start `$MAll` frames. Stop them with `raw '$upload:0,0,0#'`.
   - `yahboom_probe.py init` sends `$mtype:4#` and `$deadzone:1000#`.
4. **One motor at a time.** Run `yahboom_probe.py pwm 1 600 -d 1`, then raise the value gradually. Record:
   - the PWM value at which the track first moves (the real dead zone)
   - whether `$pwm:` is accepted. If not, try `--pwm-cmd` with another keyword, or use `raw`.
   - the direction of each track at positive PWM. This sets `--invert-left` / `--no-invert-right`.
5. **Dry-run teleop.** Run `teleop.py --dry-run`. Press START to arm, hold LB, and move the sticks. Check the signs of L and R. Test that B latches ESTOP. Unplug the pad and confirm it disarms.
6. **Live teleop, still on blocks.** Run `teleop.py --max-pwm 1200`. Raise `--max-pwm` only after step 4 confirms the full scale.

### Teleop controls

| Input | Action |
|---|---|
| START | Arm. LB must be released and the sticks centred |
| hold LB | Deadman: the robot moves only while it is held |
| Left stick Y / right stick X | Throttle / steer (`--tank`: left Y / right Y) |
| B or HOME | E-stop, latched. Re-arm with START |
| Pad lost, BT drop, pad sleep | Disarm and zero output. Reconnects automatically but stays disarmed |

## Still unverified on hardware

- The exact `$pwm:` and `$spd:` keywords, and the PWM full scale (assumed ±3600).
- Whether the board has its own command timeout. Teleop re-sends at 50 Hz either way.
- The CH340K USB ID: the udev rule covers both `1a86:7522` and `1a86:7523`.
- Whether the onboard BeagleY-AI Bluetooth works with the SN2403. The fallbacks are in the design note §5.3.
