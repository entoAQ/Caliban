#!/usr/bin/env bash
#
# Pull the latest rig code and restart the poller.
#
# This exists because the alternative -- copying files across one at a time --
# has already cost an evening. A capture.py that predated poller.py's
# expectations sat on the Pi looking perfectly normal, and the failure surfaced
# as a TypeError inside the poller rather than as "your files disagree". Files
# arriving individually can disagree; a commit cannot.
#
# Run by hand on the Pi:
#     ~/caliban/rig/update.sh
#
# and every few minutes by caliban-rig-update.timer, which is why the two
# behaviours below matter more than they would for a script only ever typed:
#
#   It restarts only when the pull actually changed something. Restarting
#   unconditionally every five minutes would interrupt captures forever, for
#   nothing. It also stops the hand-run case from killing the camera out from
#   under someone in the middle of bench work.
#
#   It defers entirely while a capture is in flight. Pulling but not
#   restarting would leave files on disk that disagree with the running
#   process -- exactly the hazard git deployment was adopted to remove -- so
#   the whole update waits for the next cycle instead.

set -euo pipefail

cd "$(dirname "$0")"

BUSY_FILE="${HOME}/.caliban-rig-busy"

# A stale busy file, left by a poller that was killed mid-capture, must not
# block updates forever. Captures take seconds; anything older than this is a
# leftover rather than work in progress.
BUSY_STALE_SECONDS=180

if [ -f "$BUSY_FILE" ]; then
    age=$(( $(date +%s) - $(stat -c %Y "$BUSY_FILE") ))
    if [ "$age" -lt "$BUSY_STALE_SECONDS" ]; then
        echo "Capture in progress (${age}s) -- deferring update."
        exit 0
    fi
    echo "Ignoring a stale busy file (${age}s old)."
fi

before=$(git rev-parse HEAD)

# --ff-only rather than a plain pull: the Pi is a consumer of this repo, never
# an author. If it somehow has local commits, that is a situation to look at
# rather than to resolve automatically with a merge nobody will ever read.
git pull --ff-only --quiet

after=$(git rev-parse HEAD)

if [ "$before" = "$after" ]; then
    echo "Already up to date at ${after:0:7}."
    exit 0
fi

echo "Updated ${before:0:7} -> ${after:0:7}:"
git --no-pager log --oneline "$before..$after"

# Only restart if the service is actually installed, so this stays useful
# during setup and on any second Pi that is only being used interactively.
if systemctl list-unit-files caliban-rig.service >/dev/null 2>&1; then
    sudo systemctl restart caliban-rig
    echo
    systemctl --no-pager --lines=0 status caliban-rig
fi
