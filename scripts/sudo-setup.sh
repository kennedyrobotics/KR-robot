#!/usr/bin/env bash
# One-time root setup on the BeagleY-AI. Run interactively (sudo prompts for a password):
#   bash ~/krc-robot/scripts/sudo-setup.sh            # serial + gamepad
#   bash ~/krc-robot/scripts/sudo-setup.sh --bluetooth  # also enable bluetooth.service
#
# The gamepad link is the CSR8510 USB dongle (Classic BT). The onboard CC3301's BLE stays OFF:
# with it enabled and the dongle plugged in at boot, btti_uart oopses ~20 s in and wedges the boot
# (notes/bluetooth-debugging.md §7). krc-ble-enable.service is kept in systemd/ but not installed.
set -e
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> Packages (evtest for manual checks, pyserial for the motor driver)..."
sudo apt-get install -y evtest python3-serial python3-tk

echo "==> udev rule -> /dev/krc-motor ..."
sudo install -m 0644 "${APP_DIR}/udev/99-krc-robot.rules" /etc/udev/rules.d/99-krc-robot.rules
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=tty

echo "==> Groups for ${USER}: dialout, input ..."
sudo usermod -aG dialout,input "${USER}"

echo "==> systemd unit (installed, NOT enabled) ..."
sudo install -m 0644 "${APP_DIR}/systemd/krc-teleop.service" /etc/systemd/system/krc-teleop.service
sudo systemctl daemon-reload

if [ "${1:-}" = "--bluetooth" ]; then
  echo "==> Bluetooth service ..."
  sudo systemctl enable --now bluetooth
  command -v rfkill &>/dev/null && sudo rfkill unblock bluetooth || true
  if systemctl is-enabled --quiet krc-ble-enable 2>/dev/null; then
    echo "==> Disabling krc-ble-enable (onboard BLE crashes the boot alongside the USB dongle; reboot to clear) ..."
    sudo systemctl disable krc-ble-enable
  fi
  ls /sys/class/bluetooth 2>/dev/null | grep -q hci && echo "    adapter present" \
    || echo "    no adapter — plug in the CSR8510 USB dongle"
fi

cat <<EOF

Done. If groups changed, log out/in (or reboot) before using the tools.
Start krbot at boot only once bench-tested (no sudo needed; linger starts it without a login):
  systemctl --user enable krbot && loginctl enable-linger
  journalctl --user -u krbot -f
EOF
