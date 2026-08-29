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

# Metered on the empty tray purely so the flat field and the white balance
# have a sane exposure to work at. Biased down a little because a bare pale
# tray under an exposure meant for anything else tends to clip, and a clipped
# pixel has lost the very brightness difference the flat field is measuring.
python3 capture.py measure --ev -0.5 --reset-white-balance
python3 capture.py flatfield

# The centre of the frame, which on an empty tray is guaranteed to be tray.
# No region to choose and nothing to place, which is the point -- a daily
# procedure that needs a judgement call is a daily procedure that drifts.
python3 capture.py whitebalance --region 0.35,0.35,0.65,0.65

echo
echo "--- geometry check against the stored reference ---"
python3 tof.py read || echo "(ToF check skipped or failed -- not fatal)"

step "2 of 3   FOCUS SHEET laid flat in the tray"

python3 capture.py focus

step "3 of 3   FILLED tray, scattered as you would for a real sample"

# Keeps the white balance measured in stage 1: it describes the light, not the
# exposure, and a bare tray is a far better neutral reference than AWB's guess
# from a frame full of tan larvae.
python3 capture.py measure

echo
echo "--- test frame ---"
python3 capture.py capture CALIB

echo
echo "Calibration complete. Current settings:"
cat ~/rig_settings.json
