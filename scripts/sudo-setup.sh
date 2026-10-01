#!/usr/bin/env bash
# One-time root setup on the BeagleY-AI. Run interactively (sudo prompts for a password):
#   bash ~/krc-robot/scripts/sudo-setup.sh            # serial + gamepad
#   bash ~/krc-robot/scripts/sudo-setup.sh --bluetooth  # also enable bluetooth.service
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
  sudo rfkill unblock bluetooth || true
fi

cat <<EOF

Done. If groups changed, log out/in (or reboot) before using the tools.
Auto-start teleop on boot only once bench-tested:
  sudo systemctl enable --now krc-teleop
  journalctl -u krc-teleop -f
EOF
