# Inline rig

A camera over the product chute after the destoner, photographing it every
few minutes, and a touchscreen beside the destoner telling the operator what
to do about it. The tray rig in `..` is sampled by hand; this one samples
itself.

| | |
|---|---|
| Pi | `Ariel`, user `ttownshend` |
| Display | Raspberry Pi Touch Display 2, portrait 720×1280, on CAM/DISP 1 |
| Camera | Camera Module 3 (standard) on CAM/DISP 0, under an upturned tote as a light shroud |
| Light | Godox TT520III at 1/128, fired by an NPN transistor (S8050) from GPIO 17 |

**Flash trigger wiring.** GPIO 17 (pin 11) → 1 kΩ → base; emitter → GND
(pin 9) and the flash cable's sleeve; collector → the cable's tip. On the
cable in use, **black is the tip and red the sleeve** -- the reverse of the
usual colours. A PC817 optocoupler was tried first (2026-10-05/06) and fired
the flash only intermittently, two chips, any wiring: driven from a Pi pin it
did not short the flash's trigger hard enough. The transistor fires it every
time. It gives up isolation between Pi and flash, acceptable at this flash's
low trigger voltage. Keep it off the first rows of the breadboard (loose
contacts), and solder it for the line -- push-fit joints do not survive the
destoner's vibration.

**Status:** screen, capture loop and the Caliban side are written and tested
against stand-ins. The camera part of the loop is provisional (a plain still,
no flash sync) until the Camera Module 3 and the flash are on the bench, and
every current prompt describes a dish, not a chute -- so readings run in
**trial mode** until a chute prompt exists and has been checked against tray
samples and lab results.

```
 loop.py ── photo ──> Caliban /inline/readings ── reading + instruction ──┐
    │                                                                    │
    └── writes ~/inline_state.json <──────────────────────────────────────┘
                        │
               screen.py ── localhost:8090 ──> touchscreen
                        │
          operator taps ──> ~/inline_acks.jsonl ── loop.py ──> Caliban /inline/acks
```

## Trial mode

`system_config.inline_settings.mode` is `shadow` unless set to `live`. In
shadow every reading is still made and recorded -- including the AUGMENTER run
that would alert AQ -- but the screen shows **MODE ESSAI** with what the rig
would have said, offers nothing to act on, and nothing goes to Teams. Switch to
`live` only once the readings have held up against the tray's and the lab's.

---

## The destoner screen

`screen.py` serves `screen.html` on `127.0.0.1:8090`, and `kiosk.sh` opens it
full-screen. The Pi serves its own screen rather than pointing at SGSC: no
account to log into, nothing to log out of, and it still says something useful
when the network is down. `screen.py`'s docstring has the full reasoning.

It shows the current instruction (same wording and colours as the tray
operator screen), recent readings against the decrease and increase lines (4 % and 6 % since
2026-10-20, from `inline_settings`), the last
photo, and the health of camera, flash and network. A reading older than 15
minutes has its instruction withdrawn.

AUGMENTER and DIMINUER ask the operator to confirm. They say how far to turn
the destoner's handle: DIMINUER (under 4 %) 2 cm clockwise; AUGMENTER 2 cm
anti-clockwise from 6 %, 4 cm from 10 %; 4-6 % no change (`INSTRUCTIONS` in
`screen.html`, thresholds in `handle_cm.sql`). When the instruction names
a value ("Régler le destoner à 4,2 psi"), the operator sets it by the
destoner's own gauge and confirms; that value then stays in the "Réglage
destoner" bar at the top until the next one, so anyone passing can check the
gauge still agrees. The destoner has a physical gauge only -- nothing reads it
automatically.

### Files

| | |
|---|---|
| `~/inline_state.json` | What the screen shows. Written by `loop.py`; read by `screen.py`. |
| `~/inline_acks.jsonl` | One line per operator confirmation. Written by `screen.py`; forwarded to Caliban by `loop.py`. |
| `~/inline_acks.sent` | How many of those lines Caliban has confirmed receiving. |
| `~/captures/inline/` | Full-resolution photos. Caliban keeps only a review copy. |

`inline_state.json`, as the screen reads it:

```json
{
  "reading": {
    "id": "IL-20261003-140500",
    "at": "2026-10-03T18:05:00+00:00",
    "band": "8-10%",
    "estimate_pct": 8.9,
    "instruction": "increase_minor",
    "alert": false,
    "target": {"value": 4.2, "unit": "psi"},
    "photo": "/home/ttownshend/captures/inline/IL-20261003-140500.jpg"
  },
  "mode": "shadow",
  "history": [{"at": "...", "estimate_pct": 7.9, "instruction": "hold"}],
  "health": {"camera": "ok", "flash": "low", "network": "ok",
             "message": "Piles du flash faibles — changer les piles"},
  "next_at": "2026-10-03T18:10:00+00:00"
}
```

`instruction` uses the tray screen's keys: `hold`, `decrease`, `increase`,
`increase_minor`, `increase_medium`, `increase_major`. `target` is optional;
without one the confirm button is the plain "Réglage fait". The setting bar
is not part of this file: `screen.py` takes it from the last `set` line in
`~/inline_acks.jsonl`, so it survives restarts.
Health values are `ok`, `low` or anything else for a fault. Write the file
atomically (write a temp file, then rename) so the screen never reads half of
one.

## The capture loop

`loop.py`, as `caliban-inline`: every `interval_min` (Caliban's setting, 5 by
default) it takes a photo, sends it to `/inline/readings`, and writes the reply
into `~/inline_state.json`. Every 5 s it forwards new confirmations to
`/inline/acks`. Camera, flash and network trouble show on the screen's chips.

`take_photo()` is the provisional part: `rpicam-still`, no flash. The real
capture -- a long exposure with the flash fired while every row is exposing --
gets written on the bench. `INLINE_FAKE_PHOTO=/path/to.jpg` sends a fixed file
instead of using the camera, to exercise the whole chain without one.

**Flash batteries** are watched against the empty-chute reference
(`flashcam.py reference`): every photo's bare-chute tiles, picked by colour,
are compared with the same tiles then. ~1.0 is healthy; two readings in a row
under 0.85 show "changer les piles du flash". During a stop the 8-minute
keep-alive is a real flash photo, so a flash that has gone to sleep shows as a
fault before the line restarts, not after. Coverage is corrected for the
measured flash strength, so a tiring flash does not read as product.

## Calibration

Order: **focus**, then settle the flash's diffusion, then **white balance**.
Stop the loop first for all of them (`sudo systemctl stop caliban-inline`):
they need the camera and the flash.

`python3 flashcam.py focus` -- autofocus is off for readings, so every photo
is focused the same; this chooses that fixed position. It takes flash photos
across the lens range (~25 flashes, a minute or two), scores each for
sharpness and saves the best as `lens_position`. Put product, or anything
with fine detail, where the stream will be first. Redo it if the camera's
height over the chute changes.

`python3 flashcam.py daylight` -- **colour, no card needed.** A camera flash is
daylight-coloured (~5600 K), and the camera's own libcamera tuning file holds
the correction its automatic white balance uses for that light; this reads it
and saves it as `colour_gains`. Use this first. On the HQ camera a white card
under the flash proved unusable (2026-10-13): green and blue clipped while red
read ~0, with no aperture setting in between.

`python3 flashcam.py wb` -- white balance **under the flash** from a card, to
refine `daylight` once a proper **grey** card is to hand: hold a white or
grey card under the camera, filling the centre of the view, and it sets
`colour_gains` in `~/inline_camera.json` so the card comes out neutral. Redo
it whenever the camera, the flash, its diffuser or the tote lining changes.
Stop the loop first (`sudo systemctl stop caliban-inline`): both need the
camera and the flash.

**The camera fitted until further notice is the Camera Module 3 Wide NoIR**
(detected as `imx708_wide_noir`). Without an infrared filter, the flash's
infrared tints the photos and white balance only partly corrects it, since
each material reflects infrared differently. Fine for timing, framing,
lighting and building the chute prompt; **not** for calibrating bands. Note
the date it is swapped for the standard Camera Module 3 (SC0872) -- readings
before that date are NoIR readings.

`flashcam.py`'s test capture also prints `clipped_pct`, the share of the frame
blown out in some channel. Keep it to a few percent: add diffusion over the
flash or bounce it off the lined wall if it is higher.

## Caliban side

- `POST /inline/readings` -- photo + reading id, judged by `analyse_band_photo`
  (the same code as a tray capture) with the operator's thresholds and prompts
  unless `inline_settings` overrides them. Recorded in `vision_band_estimates`
  with `source = 'inline'`; a retry with the same id returns the recorded
  reading instead of analysing twice.
- `POST /inline/acks` -- confirmations, into `inline_acks` (`inline_acks.sql`).
- Both need the `X-API-Key` header to match the **`INLINE_API_KEY`** app
  setting -- a key of its own, not the tray rig's, because this one can spend
  Azure calls.

**Cost:** a reading every 5 minutes is ~290 a day, each `repeats` calls per
prompt (2 by default, more when escalated). Lower it with `interval_min` or
`repeats` in `inline_settings` -- no redeploy needed.

## Setup

**Once, in Supabase:** run `inline_acks.sql`.

**Once, in Azure** (Caliban App Service → Settings → Environment variables):
add `INLINE_API_KEY`, a long random string (`python3 -c "import secrets;
print(secrets.token_urlsafe(32))"`). Saving restarts Caliban.

**On the Pi**, once `~/caliban` is cloned (same deploy-key method as the tray
rig, see `../README.md`):

```bash
# The loop's settings -- same key as in Azure.
sudo tee /etc/caliban-inline.env >/dev/null <<'EOF'
CALIBAN_URL=https://caliban-ascchkhycdeuf9ew.canadacentral-01.azurewebsites.net
INLINE_API_KEY=paste-the-key-here
EOF
sudo chmod 600 /etc/caliban-inline.env

sudo cp ~/caliban/rig/inline/caliban-inline.service /etc/systemd/system/
sudo cp ~/caliban/rig/inline/caliban-inline-screen.service /etc/systemd/system/
sudo install -m 440 -o root -g root ~/caliban/rig/inline/caliban-inline.sudoers /etc/sudoers.d/caliban-inline
sudo systemctl daemon-reload
sudo systemctl enable --now caliban-inline-screen
# Only once the camera is fitted (or INLINE_FAKE_PHOTO is set in the env file):
sudo systemctl enable --now caliban-inline


chmod +x ~/caliban/rig/inline/kiosk.sh
echo '/usr/bin/lwrespawn /home/ttownshend/caliban/rig/inline/kiosk.sh &' > ~/.config/labwc/autostart
sudo raspi-config nonint do_blanking 1
sudo raspi-config nonint do_boot_behaviour B4
sudo reboot
```

For updates, install `../caliban-rig-update.service` and its timer as on the
tray rig: `update.sh` restarts `caliban-inline` and `caliban-inline-screen`
when they are installed. An open page reloads itself when `screen.html`
changes.

## Diagnostic and repair

`doctor.py`, as `caliban-doctor` on `127.0.0.1:8091`: twelve checks (power,
disk, clock, Wi-Fi, Caliban, the key, software version, screen, kiosk, loop,
camera, flash), each with a one-button fix where a button can fix it and a
plain-French instruction where hands are needed. Its own service, standard
library only, so it is up when what it repairs is not. The docstring has the
reasoning.

| Who | Where | How |
|---|---|---|
| Operator at the destoner | the touchscreen | long press (2 s) on the Caméra/Flash/Réseau chips. The screen also hands over to it by itself after a minute of `screen.py` not answering, and `kiosk.sh` opens it if the screen never came up. It returns to the screen on its own once idle. |
| AQ, from a desk | SGSC → *Diagnostic des bancs* (`sgsc-app/rig-diagnostic.html`) | the doctor posts its checks to Caliban every minute (`/inline/doctor/report`); a button there queues a repair (`device_commands`) that the reply hands back. A repair not picked up within 10 minutes is dropped, never run late. |
| Anyone, Caliban down | GitHub → Actions | **Caliban — vérifier** (`/health` + `/health/deep`) and **Caliban — revenir à la version précédente** (redeploys the previous build's image; main is untouched, so revert the bad commit before the next push). |
| Last resort | Pi Connect shell | `bash ~/caliban/rig/inline/install-doctor.sh` reinstalls every unit and sudo rule, re-enables the updater and kiosk, restarts everything and prints the checks. `python3 ~/caliban/rig/inline/doctor.py check` only prints them. |

Fixes it can run, from either side, and nothing else: restart the loop, the
screen or the browser, restore the kiosk autostart, test the flash (stops the
loop for the shot, restarts it only if it was running), free disk (oldest
captures older than 3 days), reset the checkout to GitHub, roll back to the
last version that ran 30 minutes clean (pausing updates), send the journal,
take a control photo, reboot. Plus, on the touchscreen only, cycle the Wi-Fi.
None changes a setting or a key. `../REMOTE-REPAIR.md` sorts every known
failure of Caliban and both rigs into fixes-itself / button / desk / site.

**When the network drops.** Both ends notice. On Ariel, the tray rig's
watchdog (`../netwatch.sh`, installed by `install-doctor.sh`) cycles the
Wi-Fi at 5 minutes offline and reboots at 15, and the destoner screen shows a
red "PAS DE CONNEXION depuis…" banner saying what the watchdog has done and
will do -- only while offline. In Caliban, a watcher marks Ariel offline after
10 minutes without a report (`device_status.offline_since`,
`device_offline.sql`) and clears it on the next one; Teams hears about both
only when the rig is live, or when `inline_settings.offline_alert` is `true`.

Setup, once: run `device_doctor.sql` and `device_offline.sql` in Supabase, deploy Caliban, then on the
Pi `bash ~/caliban/rig/inline/install-doctor.sh`. `update.sh` restarts the
doctor after every pull from then on; the sudo rules are root-owned copies
and only change when that script is run again.

## Getting out of the kiosk

With a USB keyboard, Alt+F4 closes the browser, but `lwrespawn` reopens it. To
stop it for good, comment out the line in `~/.config/labwc/autostart` (Pi
Connect shell or SSH) and reboot.

## Checking it from a laptop

```bash
ssh ttownshend@Ariel.local 'curl -s localhost:8090/state'
journalctl -u caliban-inline -f             # on the Pi: every reading, every forward
journalctl -u caliban-inline-screen -f      # confirmations as they are tapped
```
