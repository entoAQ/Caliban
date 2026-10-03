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
| Light | Godox TT520III at 1/128, fired through a PC817 from a Pi GPIO |

**Status:** the destoner screen exists. The capture loop that feeds it does
not yet, so the screen runs in demo mode until it does.

---

## The destoner screen

`screen.py` serves `screen.html` on `127.0.0.1:8090`, and `kiosk.sh` opens it
full-screen. The Pi serves its own screen rather than pointing at SGSC: no
account to log into, nothing to log out of, and it still says something useful
when the network is down. `screen.py`'s docstring has the full reasoning.

It shows the current instruction (same wording and colours as the tray
operator screen), recent readings against the 3 % and 8 % lines, the last
photo, and the health of camera, flash and network. A reading older than 15
minutes has its instruction withdrawn.

AUGMENTER and DIMINUER ask the operator to confirm. When the instruction names
a value ("Régler le destoner à 4,2 psi"), the operator sets it by the
destoner's own gauge and confirms; that value then stays in the "Réglage
destoner" bar at the top until the next one, so anyone passing can check the
gauge still agrees. The destoner has a physical gauge only -- nothing reads it
automatically.

### Files

| | |
|---|---|
| `~/inline_state.json` | What the screen shows. Written by the capture loop (to come); read by `screen.py`. |
| `~/inline_acks.jsonl` | One line per operator confirmation. Written by `screen.py`; forwarded to Caliban by the capture loop (to come). |

`inline_state.json`, as the screen reads it:

```json
{
  "reading": {
    "id": "IL-20261003-140500",
    "at": "2026-10-03T18:05:00+00:00",
    "band": "8-10",
    "estimate_pct": 8.9,
    "instruction": "increase_minor",
    "alert": false,
    "target": {"value": 4.2, "unit": "psi"},
    "photo": "/home/ttownshend/captures/IL-20261003-140500.jpg"
  },
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

### Setup on the Pi

Once `~/caliban` is cloned (same deploy-key method as the tray rig, see
`../README.md`):

```bash
sudo cp ~/caliban/rig/inline/caliban-inline-screen.service /etc/systemd/system/
sudo install -m 440 -o root -g root ~/caliban/rig/inline/caliban-inline.sudoers /etc/sudoers.d/caliban-inline
sudo systemctl daemon-reload
sudo systemctl enable --now caliban-inline-screen

# Demo mode until the capture loop exists:
sudo systemctl edit caliban-inline-screen
#   [Service]
#   ExecStart=
#   ExecStart=/usr/bin/python3 /home/ttownshend/caliban/rig/inline/screen.py --demo

chmod +x ~/caliban/rig/inline/kiosk.sh
echo '/usr/bin/lwrespawn /home/ttownshend/caliban/rig/inline/kiosk.sh &' > ~/.config/labwc/autostart
sudo raspi-config nonint do_blanking 1
sudo raspi-config nonint do_boot_behaviour B4
sudo reboot
```

For updates, install `../caliban-rig-update.service` and its timer as on the
tray rig: `update.sh` restarts `caliban-inline-screen` when it is installed. An
open page reloads itself when `screen.html` changes.

### Getting out of the kiosk

With a USB keyboard, Alt+F4 closes the browser, but `lwrespawn` reopens it. To
stop it for good, comment out the line in `~/.config/labwc/autostart` (Pi
Connect shell or SSH) and reboot.

### Checking it from a laptop

```bash
ssh ttownshend@Ariel.local 'curl -s localhost:8090/state'
journalctl -u caliban-inline-screen -f      # on the Pi; acks are logged
```
