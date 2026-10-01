#!/usr/bin/env bash
# Post-deploy setup — runs ON the BeagleY-AI as the normal user (no sudo).
# Called by deploy.ps1; safe to re-run.
set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
chmod +x "${APP_DIR}"/tools/*.py "${APP_DIR}"/scripts/*.sh

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
  bash ${APP_DIR}/scripts/krc-diag.sh                 # what's connected / what's missing
  ${APP_DIR}/tools/joy_test.py --list                 # gamepad axis ranges
  ${APP_DIR}/tools/joy_test.py                        # live stick/button view
  ${APP_DIR}/tools/yahboom_probe.py listen            # sniff the motor board
  ${APP_DIR}/tools/yahboom_probe.py pwm 1 600         # spin M1 briefly (TRACKS OFF THE GROUND)
  ${APP_DIR}/tools/teleop.py --dry-run                # check mixing with no motor output
  ${APP_DIR}/tools/teleop.py                          # drive
EOF
