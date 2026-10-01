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
| Fix | `echo 1 > /sys/kernel/debug/ieee80211/phy0/cc33xx/ble_enable`, made persistent by `systemd/krc-ble-enable.service` |
| Onboard radio capability | **BLE only.** No Classic (BR/EDR). The SN2403 pairs over it only if its "Xbox Wireless Controller" emulation uses BLE |
| Pad over BLE | **Not yet tested.** Use `scripts/bt-pair-gamepad.sh` |

---

## 1. Wired USB: controller "not showing up"

### Symptom

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

### Symptom

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

**Persistence.** debugfs values reset on reboot. [`systemd/krc-ble-enable.service`](../systemd/krc-ble-enable.service) is a oneshot that runs before `bluetooth.service`. It waits up to 60 s for the Wi-Fi firmware to create the debugfs entry and then sets it. `scripts/sudo-setup.sh --bluetooth` installs and enables it.

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

- [ ] Run the BLE pairing test with the SN2403 and record whether the pad appears in the LE scan
- [ ] Confirm `krc-ble-enable.service` brings `hci0` up after a **full reboot**. So far it has only been tested by restarting the service
- [ ] If BLE pairing fails: choose a fallback (Classic dongle + PS4 mode, or 8BitDo adapter) and test it
- [ ] Check the BLE link's range and latency against the 50 Hz teleop loop once paired
