#!/usr/bin/env bash
# One-time root setup for the KR-bot Web Monitor. Run interactively (sudo prompts for a password):
#   bash ~/krc-robot/scripts/web-setup.sh
#
# - nginx: installs web/nginx-krbot.conf as the default site (the stock "default" site is unlinked,
#   not deleted) and proxies :80 to krbot_web.py on 127.0.0.1:8765.
# - ufw: allows TCP 80 only from the local subnets the robot is on (eth0 / wlan0), not from anywhere.
# The krbot-web user service itself is installed and enabled by remote-setup.sh (no sudo needed).
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"

echo "==> nginx site ..."
sudo install -m 0644 "${APP_DIR}/web/nginx-krbot.conf" /etc/nginx/sites-available/krbot
sudo ln -sf /etc/nginx/sites-available/krbot /etc/nginx/sites-enabled/krbot
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx

echo "==> ufw: allow HTTP from local subnets ..."
SUBNETS="$(ip -4 route show proto kernel scope link | awk '$3=="eth0" || $3=="wlan0" {print $1}' | sort -u)"
[ -n "$SUBNETS" ] || { echo "    no eth0/wlan0 subnet found - is the network up?"; exit 1; }
for net in $SUBNETS; do
  sudo ufw allow from "$net" to any port 80 proto tcp comment "krbot web monitor"
done
sudo ufw status | grep -E "^80|80/tcp" || true

IP="$(ip -4 -o addr show wlan0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1)"
cat <<EOF

Done. Open http://${IP:-<robot-ip>}/ from a browser on the same network.
Undo: sudo rm /etc/nginx/sites-enabled/krbot && sudo ln -s /etc/nginx/sites-available/default /etc/nginx/sites-enabled/ \\
      && sudo systemctl reload nginx;  sudo ufw status numbered  (then: sudo ufw delete <n>)
EOF
