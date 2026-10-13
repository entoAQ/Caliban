#!/usr/bin/env bash
#
# Open the destoner screen full-screen on the touch display.
#
# Started from the desktop session's autostart, wrapped in lwrespawn so a
# crashed or closed browser comes straight back:
#
#     echo '/usr/bin/lwrespawn /home/ttownshend/caliban/rig/inline/kiosk.sh &' \
#         > ~/.config/labwc/autostart
#
# The flags live here rather than in the autostart line so they reach the Pi
# through git like everything else, instead of being retyped on the device.

URL="http://127.0.0.1:8090/"
DOCTOR_URL="http://127.0.0.1:8091/?from=screen"

# The desktop can come up before screen.py does. Chromium given a dead address
# shows an error page and never retries, so wait for the server first.
#
# If it never comes, open the diagnostic (doctor.py) instead: it can restart
# the screen with a button, and goes back to it by itself once it answers.
# An error page would just sit there.
up=""
for _ in $(seq 1 60); do
    curl -s -o /dev/null "$URL" && { up=1; break; }
    sleep 1
done
if [ -z "$up" ] && curl -s -o /dev/null "http://127.0.0.1:8091/"; then
    URL="$DOCTOR_URL"
fi

BROWSER="$(command -v chromium || command -v chromium-browser)"

# --kiosk                         full screen, no address bar, no tabs
# --disable-pinch                 a two-finger brush does not zoom the page
# --overscroll-history-navigation=0   a sideways swipe does not go "back"
# --password-store=basic          no keyring prompt on a screen with no keyboard
# --disable-features=Translate    no "translate this page?" bar over French text
# --noerrdialogs --disable-infobars --no-first-run   nothing pops up over the page
exec "$BROWSER" \
    --kiosk \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --password-store=basic \
    --disable-features=Translate \
    --noerrdialogs \
    --disable-infobars \
    --no-first-run \
    "$URL"
