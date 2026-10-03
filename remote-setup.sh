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

echo "==> Installing krbot user service + 'KR-bot Monitor' shortcut..."
mkdir -p "${HOME}/.config/systemd/user"
install -m 0644 "${APP_DIR}/systemd/krbot.service" "${HOME}/.config/systemd/user/krbot.service"
systemctl --user daemon-reload 2>/dev/null || echo "    (no user systemd session - run 'systemctl --user daemon-reload' after login)"
chmod +x "${APP_DIR}/krbot_monitor_gui.py"
MONITOR_ENTRY="[Desktop Entry]
Type=Application
Name=KR-bot Monitor
Comment=Monitor and control the krbot C++ stack (live status, events, E-STOP, service start/stop)
Exec=/usr/bin/python3 ${APP_DIR}/krbot_monitor_gui.py
Icon=${APP_DIR}/images/krbot_console.png
Terminal=false
Categories=Development;Engineering;
StartupNotify=true"
for f in "${HOME}/Desktop/krbot-monitor.desktop" "${HOME}/.local/share/applications/krbot-monitor.desktop"; do
  echo "${MONITOR_ENTRY}" > "$f"
  chmod +x "$f"
done
command -v gio &>/dev/null && gio set "${HOME}/Desktop/krbot-monitor.desktop" metadata::trusted true 2>/dev/null || true
# the terminal console is superseded by the monitor window (script kept for ssh use)
rm -f "${HOME}/Desktop/krbot-console.desktop" "${HOME}/.local/share/applications/krbot-console.desktop"
echo "    krbot.service: $(systemctl --user is-enabled krbot 2>/dev/null || echo installed) / $(systemctl --user is-active krbot 2>/dev/null)"
[ -x "${APP_DIR}/krbot/build/krbot" ] || echo "    krbot not built yet: bash ${APP_DIR}/scripts/build-krbot.sh  (or 'u' in the console)"

# View-only (plus E-STOP), so unlike krbot it is enabled straight away. Restart picks up a new deploy.
echo "==> krbot-web user service (web monitor on 127.0.0.1:8765; nginx :80 via scripts/web-setup.sh)..."
install -m 0644 "${APP_DIR}/systemd/krbot-web.service" "${HOME}/.config/systemd/user/krbot-web.service"
if systemctl --user daemon-reload 2>/dev/null; then
  systemctl --user enable krbot-web >/dev/null 2>&1
  systemctl --user restart krbot-web
  echo "    krbot-web.service: $(systemctl --user is-enabled krbot-web) / $(systemctl --user is-active krbot-web)"
fi

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
  Desktop: "KR-bot Monitor"   -> krbot C++ stack (background service): live status, events, E-STOP, start/stop
  Over ssh: bash ${APP_DIR}/scripts/krbot-console.sh   (terminal view of the same)
  Desktop: "KR-Robot Control" -> Python bench app (stop krbot first — motor port is exclusive)
  bash ${APP_DIR}/scripts/krc-diag.sh                 # what's connected / what's missing
  ${APP_DIR}/tools/joy_test.py --list                 # gamepad axis ranges
  ${APP_DIR}/tools/joy_test.py                        # live stick/button view
  ${APP_DIR}/tools/yahboom_probe.py listen            # sniff the motor board
  ${APP_DIR}/tools/yahboom_probe.py pwm 1 600         # spin M1 briefly (TRACKS OFF THE GROUND)
  ${APP_DIR}/tools/teleop.py --dry-run                # check mixing with no motor output
  ${APP_DIR}/tools/teleop.py                          # drive
EOF
