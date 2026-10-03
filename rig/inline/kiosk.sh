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

# The desktop can come up before screen.py does. Chromium given a dead address
# shows an error page and never retries, so wait for the server first.
for _ in $(seq 1 60); do
    curl -s -o /dev/null "$URL" && break
    sleep 1
done

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
