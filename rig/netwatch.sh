#!/usr/bin/env bash
#
# Bring the Pi back onto the network without anyone having to be there.
#
# On 2026-09-30 the rig dropped off the plant Wi-Fi for over an hour. The Wi-Fi
# itself came back, but the Pi did not, and nothing short of a reboot at 21:38
# fixed it -- which needed someone on site. This does that reboot, and tries
# the cheaper fix first.
#
# Run every minute by caliban-rig-netwatch.timer, as root:
#
#   online           -> forget any outage, do nothing.
#   offline  5 min   -> cycle the Wi-Fi radio, once per outage.
#   offline 15 min   -> reboot, at most once an hour.
#
# "Online" means Caliban answered with any HTTP status at all, or failing
# that, the internet did. A Caliban outage is not the Pi's problem, and
# rebooting the Pi every hour because Azure is down would fix nothing while
# killing anything someone was doing at the bench.
#
# The hourly cap is the other half of that: if the plant Wi-Fi is down for a
# day, the Pi reboots once an hour rather than every 15 minutes, which is
# often enough to recover soon after it comes back and rare enough to be
# harmless.
#
# Installed as a copy in /usr/local/sbin, not run from the checkout: it runs
# as root, and pointing a root service at a file the auto-updater rewrites
# would hand root to anything that can push to this repo. Reinstall by hand
# after changing it (see README).

set -uo pipefail

ENV_FILE=/etc/caliban-rig.env
BUSY_FILE=/home/ttownshend/.caliban-rig-busy
OFF_FILE=/home/ttownshend/.caliban-netwatch-off

# /run is cleared at boot, so every boot starts a fresh outage count and the
# Wi-Fi cycle is available again. The reboot stamp must survive the reboot it
# records, so it lives on disk.
RUN_DIR=/run/caliban-netwatch
STATE_DIR=/var/lib/caliban-netwatch

WIFI_AFTER=$((5 * 60))
REBOOT_AFTER=$((15 * 60))
REBOOT_MIN_GAP=$((60 * 60))
BUSY_STALE_SECONDS=180

mkdir -p "$RUN_DIR" "$STATE_DIR"
now=$(date +%s)

# For commissioning the rig somewhere without network on purpose.
if [ -f "$OFF_FILE" ]; then
    exit 0
fi

CALIBAN_URL=$(sed -n 's/^CALIBAN_URL=//p' "$ENV_FILE" 2>/dev/null | tr -d '"' | sed 's:/*$::')

reachable() {
    local code
    if [ -n "$CALIBAN_URL" ]; then
        code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$CALIBAN_URL/health")
        [ "$code" != "000" ] && return 0
    fi
    code=$(curl -s -o /dev/null -m 15 -w '%{http_code}' https://www.google.com/generate_204)
    [ "$code" != "000" ]
}

if reachable; then
    if [ -f "$RUN_DIR/down_since" ]; then
        echo "back online after $(( now - $(cat "$RUN_DIR/down_since") ))s"
        rm -f "$RUN_DIR/down_since" "$RUN_DIR/wifi_cycled"
    fi
    exit 0
fi

if [ ! -f "$RUN_DIR/down_since" ]; then
    echo "$now" > "$RUN_DIR/down_since"
    echo "offline -- neither Caliban nor the internet answered"
    exit 0
fi

down_for=$(( now - $(cat "$RUN_DIR/down_since") ))

if [ "$down_for" -ge "$WIFI_AFTER" ] && [ ! -f "$RUN_DIR/wifi_cycled" ]; then
    echo "offline ${down_for}s -- cycling the Wi-Fi radio"
    touch "$RUN_DIR/wifi_cycled"
    nmcli radio wifi off
    sleep 5
    nmcli radio wifi on
    exit 0
fi

if [ "$down_for" -lt "$REBOOT_AFTER" ]; then
    exit 0
fi

last_reboot=$(cat "$STATE_DIR/last_reboot" 2>/dev/null || echo 0)
if [ $(( now - last_reboot )) -lt "$REBOOT_MIN_GAP" ]; then
    exit 0
fi

# Nothing can be captured while offline, but a capture that started just
# before the drop is still worth not cutting off.
if [ -f "$BUSY_FILE" ] && [ $(( now - $(stat -c %Y "$BUSY_FILE") )) -lt "$BUSY_STALE_SECONDS" ]; then
    echo "offline ${down_for}s -- capture in progress, deferring reboot"
    exit 0
fi

echo "offline ${down_for}s -- rebooting"
echo "$now" > "$STATE_DIR/last_reboot"
systemctl reboot
