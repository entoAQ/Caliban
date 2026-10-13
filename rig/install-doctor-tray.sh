#!/usr/bin/env bash
#
# Install the doctor on the tray rig (Prosperos) -- once, by hand, on the Pi:
#
#     bash ~/caliban/rig/install-doctor-tray.sh
#
# The tray rig's counterpart of inline/install-doctor.sh, and like it the
# "drastic measures" repair: reinstalls the doctor, the sudo rules, the
# auto-updater, the network watchdog and the hardware watchdog from the
# checkout, then prints every check. Safe to run again at any time. Changes
# no setting and no key: /etc/caliban-rig.env must already exist.

set -euo pipefail
cd "$(dirname "$0")"

if [ ! -f /etc/caliban-rig.env ]; then
    echo "/etc/caliban-rig.env is missing -- see README.md first." >&2
    exit 1
fi

git -C .. pull --ff-only --quiet || echo "(git pull failed -- installing what is here)"

sudo cp caliban-doctor-tray.service /etc/systemd/system/caliban-doctor.service
sudo cp caliban-rig-update.service caliban-rig-update.timer \
        caliban-rig-netwatch.service caliban-rig-netwatch.timer /etc/systemd/system/
sudo install -m 755 netwatch.sh /usr/local/sbin/caliban-netwatch
sudo install -m 440 -o root -g root caliban-rig.sudoers /etc/sudoers.d/caliban-rig
sudo install -m 440 -o root -g root inline/caliban-doctor.sudoers /etc/sudoers.d/caliban-doctor
sudo visudo -c >/dev/null

# Hardware watchdog: a Pi frozen hard reboots itself within 15 s. Same as
# Ariel's; see inline/install-doctor.sh.
sudo mkdir -p /etc/systemd/system.conf.d
printf '[Manager]\nRuntimeWatchdogSec=15\nRebootWatchdogSec=2min\n' | \
    sudo tee /etc/systemd/system.conf.d/caliban-watchdog.conf >/dev/null
sudo systemctl daemon-reexec

sudo systemctl daemon-reload
sudo systemctl enable --now caliban-doctor caliban-rig-update.timer caliban-rig-netwatch.timer
sudo systemctl restart caliban-doctor

echo
sleep 5
DOCTOR_DEVICE=prosperos python3 inline/doctor.py check
