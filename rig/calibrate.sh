#!/usr/bin/env bash
#
# Everyday calibration. Three tray swaps, nothing to remember.
#
#     ~/caliban/rig/calibrate.sh
#
# Each stage uses the one surface that is right for it:
#
#   empty tray   The only uniform, matte, correctly-positioned surface on the
#                bench, and the one physically present in every real capture.
#                So it defines both the illumination pattern and what counts
#                as white.
#   focus sheet  Autofocus needs contrasty detail and fails on a clean tray.
#   filled tray  Exposure has to be metered on the actual subject, and larvae
#                are far darker than an empty tray.
#
# What is deliberately NOT here:
#
#   setcrop      Framing is geometry. It changes when the boom moves, which is
#                not daily, and quietly re-cropping every morning would make
#                yesterday's captures incomparable with today's.
#   tof reference
#                Re-recording the drift baseline daily would destroy the only
#                thing it is for. A reference re-taken every morning can never
#                reveal that the boom moved overnight -- it would simply adopt
#                the new position as correct. So this checks against the
#                stored reference and never replaces it.

set -euo pipefail
cd "$(dirname "$0")"

step() {
    echo
    echo "=============================================================="
    echo "  $1"
    echo "=============================================================="
    read -rp "  Press Enter when ready (Ctrl+C to stop): "
    echo
}

# Only one process can hold the camera, and the poller will take it the moment
# somebody presses the button in SGSC.
echo "Stopping the capture poller."
sudo systemctl stop caliban-rig

restore() {
    echo
    echo "Restarting the capture poller."
    sudo systemctl start caliban-rig
}
trap restore EXIT

step "1 of 3   EMPTY tray, in its working position"

# One subcommand rather than three calls, so this script and the SGSC button
# run the same code. Two implementations of a calibration drift apart, and the
# drift shows up as the button quietly disagreeing with the bench.
python3 capture.py calib-empty

echo
echo "--- geometry check against the stored reference ---"
python3 tof.py read || echo "(ToF check skipped or failed -- not fatal)"

step "2 of 3   FOCUS SHEET laid flat in the tray"

python3 capture.py calib-focus

step "3 of 3   FILLED tray, scattered as you would for a real sample"

python3 capture.py calib-filled

echo
echo "--- test frame ---"
python3 capture.py capture CALIB

echo
echo "Calibration complete. Current settings:"
cat ~/rig_settings.json
