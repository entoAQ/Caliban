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
# Run it on the Pi:
#     ~/caliban/rig/update.sh

set -euo pipefail

cd "$(dirname "$0")"

# --ff-only rather than a plain pull: the Pi is a consumer of this repo, never
# an author. If it somehow has local commits, that is a situation to look at
# rather than to resolve automatically with a merge nobody will ever read.
git pull --ff-only

# Only restart if the service is actually installed, so this stays useful
# during setup and on any second Pi that is only being used interactively.
if systemctl list-unit-files caliban-rig.service >/dev/null 2>&1; then
    sudo systemctl restart caliban-rig
    echo
    systemctl --no-pager --lines=0 status caliban-rig
fi
