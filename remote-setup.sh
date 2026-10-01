#!/usr/bin/env bash
# Post-deploy setup — runs ON the BeagleY-AI as the normal user (no sudo).
# Called by deploy.ps1; safe to re-run.
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "${APP_DIR}"/tools/*.py "${APP_DIR}"/scripts/*.sh "${APP_DIR}"/krc_robot_gui.py

echo "==> Checking tkinter (desktop app)..."
python3 -c "import tkinter; print('    tk', tkinter.TkVersion)" \
  || echo "    tkinter MISSING — run scripts/sudo-setup.sh"

echo "==> Installing desktop shortcut..."
mkdir -p "${HOME}/Desktop" "${HOME}/.local/share/applications"
DESKTOP_ENTRY="[Desktop Entry]
Type=Application
Name=KR-Robot Control
Comment=KR-Robot teleop: SN2403 joystick -> Yahboom motor board
Exec=/usr/bin/python3 ${APP_DIR}/krc_robot_gui.py
Icon=${APP_DIR}/images/app_icon.png
Terminal=false
Categories=Development;Engineering;
StartupNotify=true"
for f in "${HOME}/Desktop/krc-robot.desktop" "${HOME}/.local/share/applications/krc-robot.desktop"; do
  echo "${DESKTOP_ENTRY}" > "$f"
  chmod +x "$f"
done
# GNOME needs the trusted flag; harmless on Xfce
command -v gio &>/dev/null && gio set "${HOME}/Desktop/krc-robot.desktop" metadata::trusted true 2>/dev/null || true
echo "    'KR-Robot Control' added to the desktop and application menu"

echo "==> Checking Python dependencies..."
python3 -c "import serial; print('    pyserial', serial.__version__)" \
  || echo "    pyserial MISSING — run scripts/sudo-setup.sh"

echo "==> Checking user groups (dialout for serial, input for gamepad)..."
for g in dialout input; do
  if id -nG | tr ' ' '\n' | grep -qx "$g"; then echo "    $g: ok"; else echo "    $g: MISSING — run scripts/sudo-setup.sh"; fi
done

echo "==> Checking one-time root setup..."
[ -f /etc/udev/rules.d/99-krc-robot.rules ] && echo "    udev rule: installed" \
  || echo "    udev rule: NOT installed — run once:  bash ${APP_DIR}/scripts/sudo-setup.sh"

cat <<EOF

=== Setup complete ===
  Desktop: double-click "KR-Robot Control"   (or: DISPLAY=:0 python3 ${APP_DIR}/krc_robot_gui.py)
  bash ${APP_DIR}/scripts/krc-diag.sh                 # what's connected / what's missing
  ${APP_DIR}/tools/joy_test.py --list                 # gamepad axis ranges
  ${APP_DIR}/tools/joy_test.py                        # live stick/button view
  ${APP_DIR}/tools/yahboom_probe.py listen            # sniff the motor board
  ${APP_DIR}/tools/yahboom_probe.py pwm 1 600         # spin M1 briefly (TRACKS OFF THE GROUND)
  ${APP_DIR}/tools/teleop.py --dry-run                # check mixing with no motor output
  ${APP_DIR}/tools/teleop.py                          # drive
EOF
