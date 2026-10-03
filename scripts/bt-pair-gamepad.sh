#!/usr/bin/env bash
# Pair the SN2403 (or any BT gamepad) with the BeagleY-AI.
#
#   bash ~/krc-robot/scripts/bt-pair-gamepad.sh          # scan 30 s, pick, pair/trust/connect
#   bash ~/krc-robot/scripts/bt-pair-gamepad.sh AA:BB:CC:DD:EE:FF
#
# Before running, on the pad: hold M 2 s -> Mode Switch -> PC -> A -> Pair/Reconnect
# (LEDs flash fast). Unplug the USB cable first or the pad stays in wired mode.
#
# Adapter: prefers a BR/EDR-capable one (the CSR8510 USB dongle, hci1 as of 2026-10-03), because the
# SN2403's "Xbox Wireless Controller" mode is Classic BT HID. The onboard CC3301 (hci0) is BLE-only
# and is used only as a fallback. Paired + trusted pads reconnect by themselves when switched on.
set -u
SCAN_S="${SCAN_S:-30}"

# btmgmt info works without sudo; pick the first controller whose settings include br/edr
CTRL=""
for h in /sys/class/bluetooth/hci*; do
  [ -e "$h" ] || continue
  i="${h##*hci}"
  info="$(timeout 5 btmgmt --index "$i" info 2>/dev/null)"
  addr="$(echo "$info" | awk '/addr /{print $2; exit}')"
  if echo "$info" | grep -q "supported settings:.*br/edr"; then CTRL="$addr"; break; fi
  [ -z "$CTRL" ] && FALLBACK="$addr"
done
if [ -z "$CTRL" ]; then
  CTRL="${FALLBACK:-}"
  [ -z "$CTRL" ] && { echo "No Bluetooth adapter. Plug in the USB dongle, or: sudo systemctl start krc-ble-enable"; exit 1; }
  echo "==> No Classic-capable adapter found - using ${CTRL} (BLE only)"
else
  echo "==> Using adapter ${CTRL} (Classic + LE)"
fi

# bluetoothctl one-shot commands always hit the *default* adapter, so feed it a session that selects ours
btctl() {  # btctl <seconds-to-keep-session-open> <command>...
  local hold="$1"; shift
  { echo "select ${CTRL}"; for c in "$@"; do echo "$c"; sleep 1; done; sleep "$hold"; echo quit; } \
    | timeout $((hold + $# + 10)) bluetoothctl 2>&1 | sed 's/\x1b\[[0-9;]*m//g; s/\r//g'
}

MAC="${1:-}"
if [ -z "$MAC" ]; then
  echo "==> Scanning for ${SCAN_S} s - put the pad in Pair mode now..."
  btctl "$SCAN_S" "power on" "pairable on" "scan on" >/dev/null
  echo "==> Devices seen:"
  mapfile -t DEVS < <(btctl 0 "devices" | sed -n 's/^Device //p' | sort -u)
  if [ ${#DEVS[@]} -eq 0 ]; then
    echo "    none - is the pad in Pair mode (LEDs flashing fast)?"
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

echo "==> Pairing ${MAC} (pair, trust, connect) ..."
btctl 15 "power on" "pairable on" "agent NoInputNoOutput" "default-agent" "scan on" "pair ${MAC}" \
  | grep -E "Pairing successful|Failed|failed|Error" || true
btctl 8 "trust ${MAC}" "connect ${MAC}" "scan off" | grep -E "Connection successful|Failed|failed|Error" || true
echo "==> Result:"
btctl 1 "info ${MAC}" | grep -E "^\s+(Name|Paired|Trusted|Connected):"
python3 "$(dirname "$0")/../tools/joystick-controller-debug.py" --info 2>/dev/null | grep -E "^/dev|bus=" \
  || echo "    no gamepad input device yet"
