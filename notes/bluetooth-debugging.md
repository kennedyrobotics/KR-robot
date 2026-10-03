# Bluetooth and gamepad debugging: BeagleY-AI + SN2403

Findings from bringing up the SN2403 gamepad on the BeagleY-AI, on 2026-10-01. The work covered the wired USB path and the onboard Bluetooth radio.

| | |
|---|---|
| Board | BeagleY-AI (TI AM67A), `beagle@192.168.1.116` |
| Image | BeagleBoard.org Debian 13 (trixie) Xfce, 2025-11-25 |
| Kernel | `6.1.83-ti-arm64-r72` |
| BlueZ | 5.82 |
| Radio | TI CC3301 (Wi-Fi 6 + Bluetooth LE). Wi-Fi over SDIO (`cc33xx_sdio`), BLE HCI over UART (`btti_uart`, `serial0-0` on `2860000.serial`) |
| Gamepad | SN2403, PC mode |

## Summary

| Finding | Result |
|---|---|
| Wired USB, PC mode (XInput) | **Works.** Enumerates as `045e:028e` "Microsoft X-Box 360 pad" through `xpad`, with rumble. Needs a USB **data** cable in a **USB-A** port |
| No Bluetooth adapter (`hci0` missing) | **Root cause found.** TI's `cc33xx` driver leaves BLE switched off (debugfs `ble_enable = 0`), so `btti` never registers `hci0` |
| Fix | `echo 1 > /sys/kernel/debug/ieee80211/phy0/cc33xx/ble_enable`, made persistent by `systemd/krc-ble-enable.service`. **Now disabled**, see the boot-hang row |
| Boot hang with dongle + onboard BLE (2026-10-03) | `btti_uart` oopses ~20 s into boot when both adapters come up together, wedging the boot. **Fix:** `krc-ble-enable` disabled; the dongle is the only adapter (§7) |
| Onboard radio capability | **BLE only.** No Classic (BR/EDR). The SN2403 pairs over it only if its "Xbox Wireless Controller" emulation uses BLE |
| Pad over BLE | **Not possible.** The pad's "Xbox Wireless Controller" mode is Classic BT HID (`045e:02e0`, firmware 0903) |
| Pad over Classic BT (2026-10-03) | **Works** via a CSR8510 A10 USB dongle (`0a12:0001`, `btusb`, `hci0` now that onboard BLE is off). Paired, trusted and connected as `/dev/input/event5`. `scripts/bt-pair-gamepad.sh` now picks the BR/EDR-capable adapter |
| Teleop jitter over BT (2026-10-03) | **Not the radio link.** BT reports arrive every ~2.3 ms with no gaps over 8.8 ms while moving, and krbot holds 50 Hz. **Cause:** the pad sends ~18 single reports per second with every button cleared while LB is held; when a 50 Hz tick reads one, the deadman reads released and the motors stop, then ramp back up (§8) |
| Button layout over BT | **Non-standard; remapped in software.** hid-generic puts HID buttons 1–10 on 0x130–0x139 and the Xbox button on KEY_MENU (0x08b). `BUTTON_REMAPS` in `krc/joystick.py` and `buttonRemapFor()` in `krbot/src/exec/InputDriver.cpp` translate to standard codes for this pad only. Measured: A–RB, LS/RS, LT/RT. Confirmed on the robot 2026-10-03 with the tracks off the ground: HOME e-stops, LB is the deadman, START arms, sticks drive the tracks as expected. BACK (unused by teleop) not confirmed; in raw captures one small button sent no event at all |

---

## 1. Wired USB: controller "not showing up"

### Symptom (wired)

The pad was plugged in and in PC mode. Nothing appeared in the app, in `/dev/input`, or in `lsusb`.

### Diagnosis

```bash
lsusb                                   # no new device
sudo dmesg | grep -iE "usb [0-9]-|new .*USB device|descriptor|error -7|over-current"
lsusb -t                                # hub ports 1-2 free, 3 = mouse dongle, 4 = keyboard
```

There were **no USB events at all** after boot: no new device, and no `device descriptor read … error -71`. When the kernel sees nothing whatsoever, the data lines aren't connected, so the cause is physical rather than a driver or mode problem.

### Cause and fix

The problem was a charge-only cable, a wrong socket, or both. Fixing it:

- Use a cable known to carry **data**. Many cables bundled with gamepads, and phone charging cables, carry power only.
- Plug into one of the **USB-A** ports. The BeagleY-AI's USB-C socket is **power input only**.
- If the pad is still in wireless mode and only charging: power it off, plug the cable in, then press HOME.

### Verified result

```text
Bus 001 Device 005: ID 045e:028e Microsoft Corp. Xbox360 Controller
input: Microsoft X-Box 360 pad as .../usb1/1-1/1-1.2/1-1.2:1.0/input/input5
/dev/input/event5: bus=USB id=045e:028e  rumble: yes
  LX/LY/RX/RY  -32768..32767  centred
  LT/RT             0..255    trigger
  DPAD_X/Y         -1..1
```

XInput mode is the right one: it gives standard `xpad` codes, which match the teleop mapping (LB = deadman, START = arm, B/HOME = e-stop). **Avoid DInput** (Windows + Menu for 3 s toggles it), because it presents as a generic HID with different codes.

---

## 2. Onboard Bluetooth: no adapter

### Symptom (Bluetooth)

- `bluetooth.service` was inactive. After `systemctl enable --now bluetooth` it ran, but `bluetoothctl list` returned nothing.
- `/sys/class/bluetooth/` was empty.
- `hciconfig -a` and a bare `bluetoothctl` **hung**. Always wrap them in `timeout`.

### Investigation

| Step | Command | Result |
|---|---|---|
| Kernel BT stack | `lsmod \| grep -E "bt\|blue"` | `bluetooth` and `btti_uart` loaded |
| Driver bound? | `readlink /sys/bus/serial/devices/serial0-0/driver` | Bound to `btti`. DT compatible is `ti,cc33xx-bt` |
| Driver state | `sudo dmesg \| grep btti` | Reaches `STATE_HW_ON` (regulator enabled) and **stops there**. Never reaches `STATE_HW_READY` |
| Firmware | `ls /lib/firmware/ti-connectivity/` | `cc33xx_fw.bin`, `cc33xx_2nd_loader.bin`, `cc33xx-conf.bin` present |
| BlueZ | `journalctl -u bluetooth -b` | Daemon starts cleanly and waits for a controller. Harmless warnings: `bnep-protocol` missing, ConfigurationDirectory mode |
| cc33xx debugfs | `sudo ls /sys/kernel/debug/ieee80211/phy0/cc33xx/` | Contains a **`ble_enable`** entry |
| BLE switch | `sudo cat …/cc33xx/ble_enable` | **`0`** |

### Root cause

On the CC33xx, BLE runs on the same firmware as Wi-Fi, which the `cc33xx` Wi-Fi driver loads. The `btti` UART driver powers the chip but waits for the firmware to send an HCI wake-up frame, and that frame only comes once the Wi-Fi driver enables BLE. On this image, `ble_enable` defaults to `0`. That leaves `btti` stuck in `STATE_HW_ON`, so `hci0` is never registered and BlueZ sees no adapter.

### Fix

```bash
echo 1 | sudo tee /sys/kernel/debug/ieee80211/phy0/cc33xx/ble_enable
```

The effect is immediate:

```text
btti serial0-0: SM: Got EVENT_HCI_WAKEUP_FRAME_RECEIVED, moving from STATE_HW_ON to STATE_HW_READY
Bluetooth: MGMT ver 1.22
Bluetooth: RFCOMM ver 1.11
$ ls /sys/class/bluetooth        ->  hci0
$ bluetoothctl list              ->  Controller 10:CA:BF:D8:1E:05 BeagleyAI [default]
```

**Persistence.** debugfs values reset on reboot. [`systemd/krc-ble-enable.service`](../systemd/krc-ble-enable.service) is a oneshot that runs before `bluetooth.service`. It waits up to 60 s for the Wi-Fi firmware to create the debugfs entry and then sets it. `scripts/sudo-setup.sh --bluetooth` used to install and enable it (it no longer does, §7). **Verified 2026-10-02 across a full reboot:** the service came up `active`, `hci0` was registered, and `bluetoothctl list` showed the controller.

> **Gotcha:** writing `1` when BLE is already on fails with `echo: I/O error` (EIO). The first version of the service hit this, retried for 60 s and then failed with a misleading "not found". The service now reads the value first and treats `1` as success.

### Capability: LE only

```text
$ sudo btmgmt info
supported settings: powered connectable discoverable bondable le advertising secure-conn ...
current settings:   powered le secure-conn
```

The supported settings include `le` but **not `br/edr`**, which matches the CC3301 datasheet (Wi-Fi 6 + Bluetooth **LE** 5.4). Consequences:

- **Classic HID gamepads cannot connect.** That includes the older Xbox One S Bluetooth, DualShock 4, and Switch Pro controllers.
- **BLE HID (HOGP) gamepads can.** Xbox Series X|S controllers on recent firmware are an example.
- The supporting pieces are in place:
  - `CONFIG_UHID=y` is built in, which HOGP needs through BlueZ.
  - `hid-microsoft` is available as a module.
  - BlueZ `input.conf` defaults are fine (`UserspaceHID=true`).

Whether the SN2403's PC-mode "Xbox Wireless Controller" uses BLE or Classic isn't known yet. The pairing test below answers that.

---

## 3. Pairing the SN2403 over BLE (still to test)

1. Unplug the USB cable. A plugged-in pad stays in wired mode.
2. On the pad: hold **M** for 2 s → Mode Switch → **PC** → **A** → **Pair/Reconnect**. The LEDs flash fast.
3. On the board: `ssh -t beagle@192.168.1.116 bash ~/krc-robot/scripts/bt-pair-gamepad.sh`. It runs a 30 s LE scan, lists the devices with likely pads marked, then pairs, trusts and connects the one you pick, and finally reports the evdev node.
4. The KR-Robot Control app should show the gamepad on bus **Bluetooth**.

**If the pad never appears in the LE scan,** its PC-mode Bluetooth is Classic. The fallbacks, from the design note §5.3:

1. **A USB Bluetooth Classic dongle,** with the pad in **PS4 (P4)** mode (`hid-playstation`) or Switch mode (`hid-nintendo`). Both modules are present. `btusb` is present too.
2. **An 8BitDo USB Wireless Adapter 2.** It presents as wired `xpad`. Third-party pads aren't guaranteed to work.
3. **The SN2403's own 2.4 GHz receiver,** in Receiver mode. It isn't supplied; it comes from the seller.
4. **Stay wired.** This works now.

**Expected behaviour once paired over BLE:** after 5 minutes idle, the pad auto-sleeps. That raises `ENODEV`, and teleop disarms (a safe stop). Press HOME to reconnect. The robot always comes back **DISARMED**.

---

## 4. Other observations

- **`rfkill` isn't installed** on this image. `sudo-setup.sh` now skips it when it's missing.
- **`cc33xx` kernel WARNING at boot** (`memcpy: detected field-spanning write … scan.c:49`, in `cc33xx_adjust_channels`). This is a known-noisy Wi-Fi scan warning. It's unrelated to BLE, and Wi-Fi still works.
- **"Deferred event dump"** lines in `dmesg` appeared before and after the fix. They seem to be cc33xx firmware event logging and don't appear to be harmful.
- **`debconf: This frontend requires a controlling tty`** comes from running `apt` over non-interactive SSH. It falls back to the noninteractive frontend and is harmless.
- **ufw** logs blocked multicast (`224.0.0.1`) from the router. It's harmless noise in `dmesg`.

## 5. Useful commands

```bash
# state at a glance (no sudo)
bash ~/krc-robot/scripts/krc-diag.sh

# BLE switch / service
sudo cat /sys/kernel/debug/ieee80211/phy0/cc33xx/ble_enable
systemctl status krc-ble-enable
journalctl -u krc-ble-enable -b

# adapter
timeout 5 bluetoothctl list
sudo timeout 8 btmgmt info          # 'le' only, no 'br/edr'
sudo dmesg | grep -E "btti|Bluetooth:"

# gamepad
python3 ~/krc-robot/tools/joystick-controller-debug.py --info
ssh -t beagle@192.168.1.116 ~/krc-robot/tools/joystick-controller-debug.py   # live dashboard
```

## 6. Open items

- [x] Run the BLE pairing test with the SN2403. Result: the pad's Xbox mode is Classic, not BLE (2026-10-03)
- [x] Confirm `krc-ble-enable.service` brings `hci0` up after a **full reboot** (verified 2026-10-02; service since disabled, §7)
- [x] Fallback chosen and tested: CSR8510 Classic USB dongle, pad in its Xbox mode (2026-10-03)
- [x] Pad reconnects by itself after a full power loss and after a clean reboot (2026-10-03)
- [x] Check the BT link's latency against the 50 Hz teleop loop: clean, max 8.8 ms gap while moving (§8, 2026-10-03)
- [x] Debounce deadman release so single-report button drop-outs don't stop the motors (§8, `deadman_release_ms = 40`)
- [ ] Re-run `tools/bt_link_probe.py` while driving and confirm zero output collapses with the stick steady
- [ ] Check whether the button drop-outs also happen wired (xpad) and in the pad's other modes (§8)
- [ ] BT range test (walk away / body in the way) once the drop-out fix is in
- [ ] Report the `btti_uart` oops (§7) upstream to BeagleBoard / TI, or retest on a newer kernel before re-enabling onboard BLE

## 7. Boot hang: onboard BLE + USB dongle at boot (2026-10-03)

### Symptom (boot hang)

With the CSR8510 dongle plugged in, the board stopped booting usefully. The screen showed only a cursor in the top-left, SSH authenticated but never opened a session, and the green LED kept its heartbeat blink. Wi-Fi still answered ping and the pad even reconnected, so it looked like a power fault.

### Diagnosis (boot hang)

`journalctl --list-boots` plus `journalctl -b -N -k` on three failed boots showed the same sequence each time, about 20 s in:

```text
Bluetooth: hci1: Opcode 0x0c03 failed: -110          # HCI_Reset timed out on one adapter
Unable to handle kernel NULL pointer dereference at virtual address 0000000000000e2c
Internal error: Oops: 0000000096000006 [#1] SMP
Workqueue: events btti_uart_tx_work [btti_uart]
pc : btti_uart_tx_work+0x6c/0xe0 [btti_uart]
```

The oops is in `btti_uart`, the driver for the **onboard** CC3301's BLE, not in `btusb`. It kills a kernel worker and leaves logind and the desktop waiting forever. (The `cc33xx_adjust_channels` WARNINGs from `iwd` scans in the same logs are noisy but harmless; they also appear on good boots.)

| Boot | Dongle | Onboard BLE | Result |
|---|---|---|---|
| Earlier 2026-10-03 | Hot-plugged after boot | On | Fine |
| 3 × boots | Plugged in at boot | On | Oops at ~20 s, boot wedged |
| Clean boot | Removed | On | Fine |
| After fix (twice, incl. a reboot) | Plugged in at boot | **Off** | Fine; pad reconnects by itself |

### Fix (boot hang)

The pad needs Classic BT, which only the dongle provides, so the onboard BLE isn't needed. `krc-ble-enable.service` is **disabled** (`sudo systemctl disable krc-ble-enable`). debugfs `ble_enable` resets to 0 on every boot, so `btti` never registers an adapter and the crashing path never runs. `scripts/sudo-setup.sh --bluetooth` no longer installs it and disables it if found. The dongle is now `hci0`.

Don't re-enable onboard BLE while the dongle is fitted unless the oops has been fixed upstream.

## 8. Teleop jitter over Bluetooth (2026-10-03)

### Symptom (jitter)

Driving over BT, the tracks surge and stutter: "intermittent Bluetooth and drive commands". The question was whether pad reports are arriving late or being missed.

### Method

[`tools/bt_link_probe.py`](../tools/bt_link_probe.py) (run with sudo, read-only) records three things side by side for 120 s while driving:

- every HID input report from the pad, read from `/dev/hidraw0` alongside krbot's evdev grab, timestamped
- krbot's monitor feed at 10 Hz: loop rate, mode changes, inputs, output, pad drops, watchdog trips
- radio once a second: BT RSSI and link quality (`hcitool`), Wi-Fi level

```bash
sudo python3 ~/krc-robot/tools/bt_link_probe.py --seconds 120   # raw data -> /tmp/bt-probe-*.json
```

Run 2026-10-03 17:10, ~103 s of driving with LB held, pad within a few metres of the robot.

### Results (jitter)

| Layer | Measured | Verdict |
|---|---|---|
| **BT link** | 58,185 reports in 120 s (**~485/s**). Gaps while the sticks were moving: p50 2.3 ms, p99 5.0 ms, **max 8.8 ms**, none over 25 ms. No link loss. Link quality 239–255 / 255, RSSI −16…0 (golden range). Wi-Fi −27 dBm on channel 6 alongside it | **Clean.** Nothing late, nothing missed |
| **Stick data** | Resolution 1/128 of half-travel. Held-steady noise: p2p median **0.000**, max 0.047, inside the 0.08 deadband. Values update every ~2.7 ms (p50) | **Clean** |
| **krbot** | Loop **50.0 Hz** throughout. No mode changes, no pad drops, no watchdog trips | **Clean** |
| **Buttons** | While LB was held, **1,875 single reports with every button cleared** (`00 00 00` in bytes 13–15), then straight back to LB held. **18.2 per second**, each lasting one report: p50 2.3 ms, max 8.8 ms. Spacing p10 14 ms, p50 32 ms, p90 96 ms. Sticks in those reports are normal | **This is the cause** |
| **Output** | In 82 of the 10 Hz snapshots, the left output fell by more than half (from ≥900) while the stick was steady. The 10 Hz snapshot misses most of them | Symptom |

Example: stick held at full reverse (`ly +1.00`), LB held throughout, target −1800 / +1800:

```text
  t (s)   ly     out L / R
  112.4  +1.00   -540  +540
  112.5  +1.00  -1080 +1080
  112.6  +1.00   -324  +324     <- collapsed to 0 on one tick, ramping back at 540 / 100 ms
  112.7  +1.00   -864  +864
  112.8  +1.00  -1404 +1404
  112.9  +1.00  -1800 +1800
  ...
  113.7  +1.00  -1800 +1800
  113.8  +1.00   -108  +108     <- again
```

### Mechanism

1. The pad (SN2403 in "Xbox Wireless Controller" mode, firmware 0903) interleaves occasional reports with **all buttons released**. This is in the HID data from the pad, not a radio fault: the reports are well-formed, arrive on time, and carry normal stick values.
2. evdev turns each one into `BTN_TL 0` then, one report later, `BTN_TL 1`.
3. krbot drains evdev once per 20 ms tick. If a tick reads between the release and the re-press (~2.4 ms out of every 20 ms, so about 1 in 8 drop-outs), `deadman` is false for that tick.
4. `TeleopController::update()` treats deadman released as stop: target 0, and **stopping is never rate-limited**, so the output drops to 0 at once. On the next tick deadman is back, and the slew limit (`slew_per_s = 3.0`, 540 PWM per 100 ms at `max_output = 1800`) ramps it back up over up to ~0.33 s.

The result is a stop–ramp cycle roughly once or twice a second while driving, which reads as jitter or missed commands.

Only button *releases* are faked, never presses. So the risk is spurious stops (and a spurious START edge if START were held), not unintended motion.

### Fix (implemented 2026-10-03)

- **Deadman release debounce** in `TeleopController` (C++ `krbot/src/exec/TeleopSafety.cpp` and Python `krc/drive.py`, kept identical): LB counts as released only after it has read released for `teleop.deadman_release_ms` (default **40 ms**, 2 control ticks; `0` restores the old instant behaviour). The longest drop-out seen was 8.8 ms. Pressing takes effect at once. E-STOP (B / HOME) and link loss are not debounced. Arming uses the debounced state, so a drop-out can't let it arm while LB is actually held.
- Unit tests (C++ and Python): a one-tick drop-out every other tick never stops the output; a real release stops on the 2nd tick; `0` is immediate; link loss stops at once; arming is refused during a drop-out.
- Safety trade-off: letting go of LB stops the robot up to ~40 ms later than now, well inside human reaction time and the 200 ms watchdog. Link loss still stops at once.
- Verify by re-running `bt_link_probe.py`: expect zero output collapses with the stick steady.

### Still unknown (jitter)

- Whether the drop-outs also happen wired (`xpad`) or in the pad's PS4 / Switch modes.
- Whether they come from the SN2403 firmware itself (a clone of the Xbox One S BT protocol) or from the pairing / host side. They look like a firmware trait: every one blanks all buttons and leaves the sticks untouched.
