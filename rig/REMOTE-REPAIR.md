# What can be fixed without someone on site

Every way Caliban, Ariel (the inline rig at the destoner) and Prosperos (the
tray rig) are known or expected to fail, sorted by who fixes it:

- **Itself**: nothing to do; it recovers on its own.
- **Button**: one press, from a desk or the destoner screen.
- **Desk**: no travel, but more than a button (a portal, Pi Connect).
- **Site**: someone has to go there.

Written 2026-10-13. When a tool changes, change its row here.

**Names on screen** (since 2026-10-16): the App Service is shown as
**Prospero** and the tray rig as **Caliban**, in SGSC's diagnostic page, the
GitHub buttons and the Pi screens. This file, the code, the database
(`device = 'prosperos'`) and Azure (App Service `Caliban`) keep the old names.

## Where the buttons are

| Button | Where | Reaches |
|---|---|---|
| **Prospero — vérifier** | GitHub → Actions | Caliban health, each dependency, last deploys |
| **Prospero — revenir à la version précédente** | GitHub → Actions | Redeploys the build before the live one |
| **Prospero — redémarrer** | GitHub → Actions | Restarts the App Service (needs `AZURE_CREDENTIALS`, below) |
| **Prospero — journaux** | GitHub → Actions | Container logs, errors first (needs `AZURE_CREDENTIALS`) |
| **Diagnostic des bancs** | SGSC menu (QC+) | Caliban's status; each rig's checks and every repair it offers |
| **Diagnostic** | Destoner touchscreen, 2 s press on the chips | Ariel's checks and repairs, plus Wi-Fi restart |

GitHub buttons work even when Caliban, SGSC and Ariel are all down. SGSC's
page needs Caliban up to reach Ariel.

## Caliban (Azure App Service)

| Failure | Noticed by | Fix | Who |
|---|---|---|---|
| A deploy broke it | Deploy workflow's live check; *vérifier*; SGSC banner | *revenir à la version précédente*, then revert the commit before the next push | Button |
| Up but wedged / slow, nothing deployed | *vérifier* (slow or failing checks) | *redémarrer* | Button |
| Need to know why | — | *journaux* | Button |
| Realtime wakeup deaf (tray captures wait 90-120 s, 2026-10-13) | `capture_queue` in *vérifier* / SGSC | Detects it and rebuilds the listener; captures wait ≤ 20 s meanwhile | Itself |
| Supabase down | `supabase` check | Nothing we can do; status.supabase.com | — |
| Vision endpoint unreachable | `vision` check | Azure status; nothing on our side | — |
| Vision key revoked / quota exhausted | **Not checked** (reachability only): readings fail with 401/429 in *journaux* | New key in Azure Portal → Caliban → Environment variables | Desk |
| An app setting deleted by a portal edit | `config` list in *vérifier* | Re-add it in the portal | Desk |
| Teams webhook broken | **Not checked**: alerts silently stop | Re-create the webhook, update `TEAMS_ALERT_WEBHOOK_URL` | Desk |
| Publish profile reset | Rollback/deploy fails | Download a new one into secret `AZURE_WEBAPP_PUBLISH_PROFILE`; meanwhile the rollback summary prints the Cloud Shell command | Desk |
| `AZURE_CREDENTIALS` expired (2 years after creation) | *redémarrer* / *journaux* fail at login | Re-run the setup command below | Desk |
| GitHub Actions down | Buttons don't start | Cloud Shell: `az webapp config container set … :<sha>` / `az webapp restart -n Caliban -g Caliban_group` | Desk |

## Ariel (inline rig, destoner)

**Recovers by itself**

| Failure | How |
|---|---|
| Loop, screen or doctor crashes | systemd restarts it (`Restart=always`) |
| Camera hangs mid-capture | Loop's watchdog exits after 120 s; systemd restarts it with a fresh camera session |
| Browser closed or crashed | `lwrespawn` reopens it |
| Screen service won't come back | The page (or `kiosk.sh` at boot) switches to the Diagnostic page, and returns when the screen answers |
| Wi-Fi drops | Watchdog (`netwatch.sh`): Wi-Fi cycled at 5 min, reboot at 15, at most hourly. The screen shows PAS DE CONNEXION meanwhile |
| Pi freezes hard (kernel hung) | Hardware watchdog: reboots within 15 s (from `install-doctor.sh`, 2026-10-13) |
| New code on GitHub | Pulled within 5 min (`caliban-rig-update.timer`), unless paused by a rollback |
| Ariel silent 10 min | Caliban marks it offline; Teams told if live or `offline_alert` |

**One button** (SGSC, or the touchscreen)

| Symptom | Button |
|---|---|
| Anything red | **Tout réparer** first |
| Readings stopped / stale | Redémarrer les lectures |
| Screen frozen or blank | Redémarrer l'écran; Rouvrir le navigateur |
| Kiosk gone after a reboot | Rétablir le plein écran (then reboot) |
| An update broke it | **Revenir à la version précédente**: back to the last version that ran 30 min without a failure; updates paused until *Remettre le logiciel à jour* |
| Stuck on old code / hand-edited | Remettre le logiciel à jour |
| Disk filling | Libérer de l'espace (photos > 3 days, oldest first) |
| "Is the tote on / lens clean / chute normal?" | **Photo de contrôle**: a flash photo, shown in SGSC |
| "What is it actually doing?" | **Envoyer le journal**: last 300 log lines + screen state, shown in SGSC |
| Flash suspected | Tester le flash |
| Anything else | Redémarrer le Pi |
| Wi-Fi up but no internet | Relancer le Wi-Fi (touchscreen only: from a desk it would cut the link it's sent over) |

**From a desk, more than a button** (Pi Connect, while Ariel is online)

- Change `/etc/caliban-inline.env` (key rotation) — by design no button edits keys.
- `bash ~/caliban/rig/inline/install-doctor.sh` — reinstalls every unit, rule and watchdog.
- Calibrations needing no hands: `flashcam.py daylight`, `flashcam.py motion`, and `flashcam.py reference` if the chute is empty and the line stopped.
- Remove a leftover `demo.conf` drop-in (demo mode no longer exists; the screen won't start with it).

**Needs someone there**

| Failure | Symptom shown | What to do |
|---|---|---|
| Flash asleep | Flash ✕, "Ne se déclenche pas" | Press a button on the flash |
| Flash batteries flat | Flash ✕ / black photo | 4 charged AAs (the "weak flash" warning is **off**: it read dust, not batteries) |
| Camera ribbon loose / camera dead | Caméra ✕ after a restart and a reboot | Re-seat the ribbon, Pi off |
| Tote shroud knocked | "Trop de lumière sous la couverture" | Put it back |
| Lens dirty / out of focus / camera moved | *Photo de contrôle* shows it | Clean / refocus (HQ camera: by hand) |
| Pi unplugged, PSU dead, undervoltage | Ariel silent; or "Tension trop basse" | Plug in / official 27 W supply |
| SD card corrupt, won't boot | Ariel silent, screen black after reboots | Reflash the card (no remote path) |
| Touchscreen dead / cable | Ariel healthy in SGSC, screen black | Check the DSI cable |
| Plant Wi-Fi down | PAS DE CONNEXION; tray rig silent too | IT, not the rigs |

## Prosperos (tray rig)

The same doctor as Ariel's since 2026-10-13 (`DOCTOR_DEVICE=prosperos`,
installed by `rig/install-doctor-tray.sh`), reporting with the tray key and
shown in its own card in SGSC → Diagnostic des bancs. No touchscreen, so its
buttons are SGSC's.

**Recovers by itself**

| Failure | How |
|---|---|
| Poller crash | systemd restarts it |
| Capture hangs | Poller watchdog restarts it |
| Wi-Fi drops | netwatch: Wi-Fi cycle at 5 min, reboot at 15 |
| Pi freezes hard | Hardware watchdog, 15 s |
| New code | Update timer, within 5 min, unless paused by a rollback |
| Captures slow to start (deaf wakeup) | Caliban's listener rebuild; `capture_queue` shows it |
| Silent 10 min | Caliban marks it offline and tells Teams (on by default for this rig: it is in daily use; `system_config.device_offline_alerts = {"prosperos": false}` turns it off) |

**One button** (SGSC)

| Symptom | Button |
|---|---|
| Anything red | Tout réparer |
| Captures time out / "Prise des photos" red | Redémarrer le service du banc |
| "Is the tray right / lit / in focus?" | Photo de contrôle — with the bench's locked settings; the poller is paused for the few seconds it takes |
| "Why did that capture fail?" | Envoyer le journal (with the calibration file) |
| An update broke it | Revenir à la version précédente |
| Stuck on old code | Remettre le logiciel à jour |
| Anything else | Redémarrer le Pi |

None of them runs during a capture: they refuse and say to try again in a minute.

**From a desk**: moving `~/captures` to SharePoint when the disk check turns
yellow (no button deletes them -- they are the only copy); key rotation in
`/etc/caliban-rig.env`; re-running `install-doctor-tray.sh`.

**Needs someone there**: daily calibration (SGSC → Calibrer le banc, with the
trays); camera ribbon or a dead camera; the height sensor's cable ("Capteur de
hauteur" yellow -- density only); the LED light; an unplugged Pi; a corrupt
SD card.

## Setting up `AZURE_CREDENTIALS`

Used by *Prospero — redémarrer* and *Prospero — journaux*. Done 2026-10-16;
it expires 2028-10, so redo it before then. In Azure Cloud Shell:

```
az ad sp create-for-rbac --name caliban-github-actions --role "Website Contributor" --years 2 \
  --scopes $(az webapp show --name Caliban --resource-group Caliban_group --query id -o tsv) --json-auth
```

Paste the whole JSON it prints into GitHub → Settings → Secrets and variables
→ Actions → New repository secret, named `AZURE_CREDENTIALS`. Scoped to the
Caliban web app only; expires in two years.

## Known gaps

- **Tray rig checks are new**: the "silent for 15 min" poller check and the
  height-sensor probe are reasoned from the code, not yet seen failing.
- **Vision key and quota are not checked**: a revoked key looks healthy until
  readings fail. A real test call costs money each time, so it is left out.
- **The Teams webhook is not checked**: a broken one fails silently.
- **Flash batteries**: no trustworthy check (dust on the chute fooled the
  first one). Candidate: flash recycle time — fire two flashes a set gap
  apart and see whether the second fires.
- **A Pi that won't boot** has no remote path. A smart plug on its supply
  would add remote power-cycling, but the hardware watchdog already covers
  the freeze it would mostly be used for.
