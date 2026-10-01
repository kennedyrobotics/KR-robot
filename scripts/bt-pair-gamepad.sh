#!/usr/bin/env bash
# Pair the SN2403 (or any BLE gamepad) with the BeagleY-AI's onboard CC3301 radio.
#
#   bash ~/krc-robot/scripts/bt-pair-gamepad.sh          # scan 30 s, pick, pair/trust/connect
#   bash ~/krc-robot/scripts/bt-pair-gamepad.sh AA:BB:CC:DD:EE:FF
#
# Before running, on the pad: hold M 2 s -> Mode Switch -> PC -> A -> Pair/Reconnect
# (LEDs flash fast). Unplug the USB cable first or the pad stays in wired mode.
#
# The CC3301 is BLE-only. If the pad never appears in the scan, its PC-mode Bluetooth is
# Classic (BR/EDR) and needs a USB BT Classic dongle instead (design note §5.3 option 2).
set -u
SCAN_S="${SCAN_S:-30}"

if [ ! -e /sys/class/bluetooth/hci0 ]; then
  echo "No hci0. Enable BLE first:  sudo systemctl start krc-ble-enable  (or run sudo-setup.sh --bluetooth)"
  exit 1
fi
timeout 5 bluetoothctl power on >/dev/null

MAC="${1:-}"
if [ -z "$MAC" ]; then
  echo "==> Scanning (LE) for ${SCAN_S} s — put the pad in Pair mode now..."
  timeout $((SCAN_S + 5)) bluetoothctl --timeout "$SCAN_S" scan le >/dev/null 2>&1
  echo "==> Devices seen:"
  mapfile -t DEVS < <(timeout 5 bluetoothctl devices | sed -n 's/^Device //p')
  if [ ${#DEVS[@]} -eq 0 ]; then
    echo "    none — pad not advertising over BLE (see note at top of this script)"
    exit 2
  fi
  i=0
  for d in "${DEVS[@]}"; do
    mark=""
    echo "$d" | grep -qiE "xbox|controller|gamepad|pad|sn2403" && mark="   <-- likely"
    printf "  [%d] %s%s\n" "$i" "$d" "$mark"
    i=$((i + 1))
  done
  read -r -p "Pick a number: " n
  MAC="$(echo "${DEVS[$n]}" | cut -d' ' -f1)"
fi

echo "==> Pairing ${MAC} ..."
timeout 30 bluetoothctl pair "$MAC"   || echo "    (pair returned non-zero — may already be paired)"
timeout 10 bluetoothctl trust "$MAC"
timeout 20 bluetoothctl connect "$MAC"
sleep 2
echo "==> Result:"
timeout 5 bluetoothctl info "$MAC" | grep -E "Name|Paired|Trusted|Connected"
python3 "$(dirname "$0")/../tools/joystick-controller-debug.py" --info 2>/dev/null | grep -E "^/dev|bus=" \
  || echo "    no gamepad input device yet"
