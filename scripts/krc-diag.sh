#!/usr/bin/env bash
# KRC-Robot bring-up diagnostics — Yahboom motor board (USB serial) + SN2403 gamepad.
# Run ON the BeagleY-AI:  bash ~/krc-robot/scripts/krc-diag.sh
# Pattern follows the atr-viu-emulator beagley-diag.sh. No sudo needed.
set -u

BLUE='\033[1;34m'; GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; NC='\033[0m'
section() { echo -e "\n${BLUE}=== $1 ===${NC}"; }
ok()      { echo -e "  ${GREEN}[OK]${NC} $1"; }
warn()    { echo -e "  ${YELLOW}[WARN]${NC} $1"; }
fail()    { echo -e "  ${RED}[FAIL]${NC} $1"; }
info()    { echo -e "  [INFO] $1"; }
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo -e "${BLUE}KRC-Robot Bring-up Diagnostics${NC}"
echo "  Board   : $(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)"
echo "  Kernel  : $(uname -r)"
echo "  Date    : $(date)"

MOTOR_OK=false; PAD_OK=false

section "USER / PERMISSIONS"
for g in dialout input bluetooth; do
  id -nG | tr ' ' '\n' | grep -qx "$g" && ok "in group $g" || fail "not in group $g (scripts/sudo-setup.sh)"
done
python3 -c "import serial" 2>/dev/null && ok "pyserial present" || fail "pyserial missing"

section "MOTOR BOARD (Yahboom YB-ESF01, CH340K)"
if lsusb | grep -qiE "1a86:752[23]"; then
  ok "CH340 on USB: $(lsusb | grep -iE '1a86:752[23]')"
else
  fail "no CH340 (1a86:7522/7523) on USB — check USB-C cable is a DATA cable and board is powered"
fi
lsmod | grep -q '^ch341' && ok "ch341 driver loaded" || warn "ch341 not loaded (autoloads on plug-in)"
if [ -e /dev/krc-motor ]; then
  ok "/dev/krc-motor -> $(readlink -f /dev/krc-motor)"; MOTOR_OK=true
elif ls /dev/ttyUSB* &>/dev/null; then
  warn "$(ls /dev/ttyUSB* | tr '\n' ' ')present but no /dev/krc-motor symlink (udev rule not installed?)"
  MOTOR_OK=true
else
  fail "no /dev/ttyUSB* node"
fi
[ -f /etc/udev/rules.d/99-krc-robot.rules ] && ok "udev rule installed" || warn "udev rule not installed"

section "GAMEPAD (SN2403)"
if lsusb | grep -qiE "045e:|xbox|controller|gamepad"; then
  ok "USB pad candidates: $(lsusb | grep -iE '045e:|xbox|controller|gamepad' | cut -d' ' -f6- | tr '\n' ';')"
else
  info "no wired pad on USB (fine if using Bluetooth)"
fi
for m in xpad hid_microsoft hid_playstation hid_nintendo joydev; do
  lsmod | grep -q "^${m} " && ok "$m loaded" || info "$m not loaded"
done
PADS="$(python3 "${APP_DIR}/tools/joy_test.py" --list 2>&1)"
if echo "$PADS" | grep -q '^/dev/input'; then
  ok "gamepad(s) visible to the tools:"; echo "$PADS" | sed 's/^/        /'; PAD_OK=true
else
  fail "no evdev gamepad found"
fi
echo "  Recent input/USB kernel messages:"
if dmesg &>/dev/null; then
  dmesg | grep -iE 'xpad|input:|ch341|ttyUSB|hid-' | tail -8 | while read -r l; do info "$l"; done
else
  info "(dmesg needs sudo on this image)"
fi

section "BLUETOOTH"
systemctl is-active --quiet bluetooth && ok "bluetooth.service active" \
  || warn "bluetooth.service inactive (scripts/sudo-setup.sh --bluetooth)"
rfkill list bluetooth 2>/dev/null | sed 's/^/        /'
CTRL="$(timeout 4 bluetoothctl list 2>/dev/null)"
[ -n "$CTRL" ] && ok "controller: $CTRL" || warn "no BT controller reported (service down, or onboard CC33xx BLE-only radio not registered)"
lsusb | grep -qi bluetooth && info "USB BT dongle: $(lsusb | grep -i bluetooth | cut -d' ' -f6-)"

section "SUMMARY"
$MOTOR_OK && ok "Motor board serial: ready" || fail "Motor board serial: not ready"
$PAD_OK   && ok "Gamepad: ready"            || fail "Gamepad: not ready"
echo
