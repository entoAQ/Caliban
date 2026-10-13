#!/usr/bin/env bash
#
# Install Ariel's diagnostic (doctor.py) -- once, by hand, on the Pi:
#
#     bash ~/caliban/rig/inline/install-doctor.sh
#
# Also the "drastic measures" repair: it reinstalls every unit and sudo rule
# the inline rig needs from the checkout, re-enables the auto-updater and the
# kiosk, and restarts everything. Safe to run again at any time. It changes no
# setting and no key: /etc/caliban-inline.env must already exist.

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f /etc/caliban-inline.env ]; then
    echo "/etc/caliban-inline.env is missing -- see README.md 'Setup' first." >&2
    exit 1
fi

git -C ../.. pull --ff-only --quiet || echo "(git pull failed -- installing what is here)"

for unit in caliban-inline.service caliban-inline-screen.service caliban-doctor.service; do
    sudo cp "$unit" /etc/systemd/system/
done
sudo cp ../caliban-rig-update.service ../caliban-rig-update.timer /etc/systemd/system/
# Network watchdog, shared with the tray rig: Wi-Fi cycle at 5 min offline,
# reboot at 15. A root-owned copy, as there (see ../netwatch.sh).
sudo install -m 755 ../netwatch.sh /usr/local/sbin/caliban-netwatch
sudo cp ../caliban-rig-netwatch.service ../caliban-rig-netwatch.timer /etc/systemd/system/
sudo install -m 440 -o root -g root caliban-inline.sudoers /etc/sudoers.d/caliban-inline
sudo install -m 440 -o root -g root caliban-doctor.sudoers /etc/sudoers.d/caliban-doctor
sudo visudo -c >/dev/null

# Hardware watchdog: systemd pets the Pi's watchdog chip, so a Pi frozen hard
# -- kernel hung, nothing left to run a repair -- reboots itself within 15 s
# instead of waiting for someone to pull the plug. The one failure no button
# could reach.
sudo mkdir -p /etc/systemd/system.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15\nRebootWatchdogSec=2min\n' | \
    sudo tee /etc/systemd/system.conf.d/caliban-watchdog.conf >/dev/null
sudo systemctl daemon-reexec

sudo systemctl daemon-reload
sudo systemctl enable --now caliban-doctor caliban-inline-screen caliban-rig-update.timer caliban-rig-netwatch.timer
sudo systemctl enable caliban-inline
sudo systemctl restart caliban-doctor caliban-inline-screen caliban-inline

chmod +x kiosk.sh
mkdir -p ~/.config/labwc
grep -q kiosk.sh ~/.config/labwc/autostart 2>/dev/null || \
    echo '/usr/bin/lwrespawn /home/ttownshend/caliban/rig/inline/kiosk.sh &' > ~/.config/labwc/autostart

echo
python3 doctor.py check
