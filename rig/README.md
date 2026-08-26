# Bench rig

Overhead camera rig for the filled-dish MEO method. A Raspberry Pi 5 with a
Camera Module 3 on a fixed boom, photographing a levelled dish of
larvae. Photos go to Caliban for a band estimate, triggered from the
**Estimation banc** button on the SGSC results entry panel.

Everything on the camera is locked — exposure, gain, white balance, focus,
crop. That is the whole point. The old webcam ran on auto through the browser,
so every frame was processed differently and there was no way to stop it; a
model asked to judge whether a dark patch is frass or shadow cannot do it
consistently under those conditions. Measure once, lock, never let the camera
think again.

Consequence: **any change to lighting or geometry invalidates the calibration.**
Move a lamp, add a shroud, raise the boom, and the locked values are wrong with
nothing to rescue them. Recalibrate.

---

## Hardware

| | |
|---|---|
| Pi | `Prosperos-island`, user `ttownshend`, wifi `172.20.202.99` (DHCP) |
| SSH | `ssh ttownshend@172.20.202.99` |
| **Live preview** | **http://172.20.202.99:8000/** — only while `python3 preview.py` is running |
| Camera | Raspberry Pi Camera Module 3, imx708, 12MP autofocus, 4608×2592 |
| Network | Wi-Fi carries the Pi; `eth0` is reserved for the NIR instrument |

The address comes from DHCP and can change. If it stops answering, try
`ttownshend@Prosperos-island.local`, or ask IT for a reservation.

`eth0` has `ipv4.never-default yes` so the default route stays on Wi-Fi. Without
it the Pi would try to reach the internet down a cable that leads only to a
spectrometer.

---

## Camera commands

Run on the Pi. Regions are `x0,y0,x1,y1` as fractions of the frame, origin
top-left.

| Command | What it does |
|---|---|
| `python3 preview.py` | Live view at `http://172.20.202.99:8000/` for framing and focus. Ctrl+C to stop. Runs **auto** exposure and white balance — it is a viewfinder, so never judge colour or brightness from it. |
| `python3 capture.py focus` | Runs autofocus once and records the lens position, in dioptres, into `~/rig_settings.json`. Every capture afterwards holds that position. **Run it before `measure`**, and again only if the boom height changes. Needs contrasty detail to lock onto, so put a printed target on the surface at tray height and take it away afterwards. |
| `python3 capture.py measure` | Meters the scene, locks exposure and gain into `~/rig_settings.json`. **Run with a filled dish** — metering an empty one drives the exposure far too high and blows out the sample. Warns above gain 2.0, which means underlit. Resets the colour gains, so always follow with `whitebalance`. |
| `python3 capture.py measure --ev -1` | Same, biased darker by a stop. `-0.5` subtler, `+1` doubles. Sparingly: if something bright is clipping it is usually better to make that thing darker than to underexpose the dish. |
| `python3 capture.py sample --region ...` | Reports mean R/G/B of a region and whether it works as a reference. Changes nothing, so guess freely. Also the ambient-drift check. |
| `python3 capture.py whitebalance --region ...` | Cancels the NoIR colour cast against a neutral patch. Iterates to convergence. Shortens the exposure internally if the patch clips — expected, and reported. |
| `python3 capture.py setcrop --region ...` | Sets the crop on saved captures. **Keep the dish rim visible** — the prompt asks the model to judge only inside the dish, which it can only do if it can see the edge. Does not affect `sample` or `whitebalance`, which always read the full sensor frame. |
| `python3 capture.py capture LOT12345` | One photo into `~/captures/`. `--band ir` for an infrared frame. |

## Service control

| Command | What it does |
|---|---|
| `sudo systemctl stop caliban-rig` | **Before any manual camera work.** Only one process can hold the camera. |
| `sudo systemctl start caliban-rig` | Restart so the SGSC button works again. |
| `~/caliban/rig/update.sh` | Pull the latest code and restart the poller. This is how code reaches the Pi. |
| `systemctl status caliban-rig` | Is it running. |
| `journalctl -u caliban-rig -f` | Watch it live. Ctrl+C stops following, not the service. |
| `cat ~/rig_settings.json` | Current calibration. |

## From a laptop (PowerShell)

| Command | What it does |
|---|---|
| `ssh ttownshend@172.20.202.99` | Connect. |
| `git push` | How code reaches the Pi — push here, then run `update.sh` there. |
| `scp ttownshend@172.20.202.99:~/captures/*.jpg <local dir>` | Pull the photos down. |

---

## Getting code onto the Pi

The Pi holds a read-only checkout of this repo at `~/caliban`. Code moves
laptop → GitHub → Pi, never laptop → Pi directly.

That is not ceremony. Copying files one at a time has already cost an evening:
a `capture.py` that predated `poller.py`'s expectations sat on the Pi looking
entirely normal, and the failure surfaced as a `TypeError` inside the poller
rather than as "your two files disagree". Files copied individually can
disagree with each other. A commit cannot.

Routine update — push from the laptop, then on the Pi:

```bash
~/caliban/rig/update.sh
```

That pulls fast-forward-only and restarts the service. The Pi is a consumer of
this repo and never an author, so if it ever refuses because it has local
commits, that is something to look at rather than merge away.

### One-time setup

On the Pi, make a key for it and print the public half:

```bash
ssh-keygen -t ed25519 -C "caliban-rig" -f ~/.ssh/id_ed25519 -N ""
cat ~/.ssh/id_ed25519.pub
```

On GitHub: **Caliban → Settings → Deploy keys → Add deploy key**. Paste it,
title it `caliban-rig`, and **leave "Allow write access" unticked**. A deploy
key is scoped to this one repository, unlike a personal token, and read-only
means a compromised bench Pi cannot rewrite the repo it deploys from.

Then clone and clear out the old loose copies, so there is exactly one
`capture.py` on the machine and no chance of running yesterday's:

```bash
git clone git@github.com:entoAQ/Caliban.git ~/caliban
chmod +x ~/caliban/rig/update.sh
rm -f ~/capture.py ~/poller.py ~/preview.py

sudo cp ~/caliban/rig/caliban-rig.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart caliban-rig
```

`~/rig_settings.json` and `~/captures/` deliberately stay outside the checkout.
The calibration is this rig's measurement of this bench — it belongs to the
machine, not to the branch, and it must survive every pull untouched.

---

## Recalibration sequence

After any change to lighting, geometry, or the enclosure:

```bash
sudo systemctl stop caliban-rig
python3 preview.py                                  # frame, Ctrl+C
python3 capture.py focus                            # target on the surface
python3 capture.py measure                          # filled dish
python3 capture.py sample --region <patch>          # confirm "usable"
python3 capture.py whitebalance --region <patch>
python3 capture.py setcrop --region <dish + margin>
python3 capture.py capture LOT12345
sudo systemctl start caliban-rig
```

Order matters twice over. `focus` comes first because the exposure, the white
balance patch and the crop are all measured through whatever focus is set, so
refocusing afterwards invalidates them the same way moving the camera does. And
`measure` overwrites the colour gains, so `whitebalance` always comes after it. Check `white_balanced_at` appears in the settings file — that
is how you know the correction applied.

## Current values

| | |
|---|---|
| Neutral patch | `0.11,0.35,0.16,0.58` — white sticker |
| Crop | `0.23,0.02,0.97,0.98` |
| Ambient drift check | `0.05,0.45,0.12,0.55` — dish rim, mid-tone, never clips |

**Save a copy of `rig_settings.json` with any corpus.** It defines what the
images mean. Without it, you cannot later tell whether two images differ
because the samples differed or because the rig was recalibrated between them.

---

## Setup

### Pi

```bash
sudo cp caliban-rig.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable caliban-rig
sudo systemctl start caliban-rig
```

`/etc/caliban-rig.env`, owned by `ttownshend`, mode 600:

```
CALIBAN_URL=https://caliban-ascchkhycdeuf9ew.canadacentral-01.azurewebsites.net
RIG_API_KEY=...
RIG_CAPTURE_IR=0
```

Kept out of the unit file so the unit can live in git without the key in it.

### Supabase

Run `capture_commands.sql` once, plus a storage read policy so the browser can
fetch what the rig uploaded:

```sql
create policy "authenticated read band test captures"
on storage.objects for select to authenticated
using (bucket_id = 'vision-band-test-captures');
```

### Caliban

Set `RIG_API_KEY` in App Service configuration to match the Pi. Deliberately a
different secret from `POWER_AUTOMATE_API_KEY`: the rig is physically
accessible hardware on a factory floor, and rotating its key after a theft
should not also break the Power Automate integration.

---

## How the trigger works

The browser cannot reach the Pi. It sits behind plant NAT on a private address,
an HTTPS page cannot call a plain-HTTP local device, and Chrome's Private
Network Access rules block it again even if it could. Every inward path fails.

So the direction is inverted. Nothing addresses the Pi — the Pi reaches out.

```
SGSC button  ──insert──>  capture_commands  <──poll──  Pi
                                │                       │
                                │                    captures
                                │                       │
                          status: done  <──upload────────┘
                                │
SGSC polls ─────────────────────┘
     │
     └──> POST /azure-band-test  (operator's own session)
```

The rig can claim and complete capture commands and nothing else. It cannot run
an analysis, read a lot, or reach any operator-facing endpoint. Analysis stays
attributed to a logged-in person rather than to a device.

The Pi never talks to Supabase directly either — it goes through Caliban, which
holds the service key. Putting a Supabase credential on a device sitting in a
factory is the thing this design exists to avoid.

## Why the lab ME% arrives late

Separating and weighing the MEO destroys the levelled dish, so the photograph
has to be taken before the number can exist. Every estimate is therefore
recorded without a real value, and `POST /band-estimates/backfill` attaches it
when the operator saves their results — called automatically from
`saveResults()` in SGSC. Only rows still missing a value are touched, so a
later lab correction is never silently overwritten.

---

## Known limits

**Colour is not fully correctable.** This is a NoIR sensor with no IR-cut
filter, so infrared reaches it in amounts that depend on what each material is
made of. White balance can neutralise the reference patch, but larvae still
read faintly mauve and a green sticker reads blue — one set of gains cannot fix
three materials shifting by different amounts. Not a tuning problem. The fix is
an IR-cut filter, i.e. a Camera Module 3 on the free CSI port.

**The surface under-represents the bulk.** Fine MEO percolates down through the
gaps between larvae, so a levelled dish shows less than it contains, and the
effect grows with MEO content. Correct this downstream as a fitted curve
against paired lab values — never in the prompt. Prompt-level directional
nudges are what 1.1 did and 1.2 had to undo; they make the correction invisible
and unfalsifiable, and you lose the ability to tell a bad estimate from a bad
correction.

**Ambient light is uncontrolled.** The lab has natural light, and locked
exposure means the camera cannot absorb changes in it. Check with the
drift-region `sample` at three times of day. Swings of twenty or thirty counts
mean a variance source sits underneath everything else being measured.

**Repeatability sets the ceiling.** No filter, prompt, or calibration curve
gets accuracy below the spread of refilling the same sample. Fill, level,
shoot, tip out, remix, five or six times. That number is worth having before
investing in anything else.
