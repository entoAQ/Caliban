#!/usr/bin/env python3
"""What Ariel's screen should say about a reading, worked out on the Pi.

Caliban has done this until now, and Caliban is being dissolved. The detector
already runs here (meo_detect.py), so all that was left up there was these rules
and a few writes. The rules below are copied from Caliban's app/main.py word for
word: operators act on these instructions, and a rewrite could change one without
anyone noticing. Only the queries differ.

Shadow first: with ARIEL_RIG_DB_URL in /etc/caliban-inline.env, loop.py works out
each reading here as well as asking Caliban, and compares the two. Caliban still
decides and records everything. The login can only read (sgsc-app
database/2026-11-01-ariel-rig-role.sql), so shadow can't touch production data.
The tally lands in the state mirrored to inline_rig_status, as "decide_shadow".

The switch: with ARIEL_DECIDES=1 as well, loop.py stops asking Caliban. record()
decides here, sends the photos to SGSC's rig-upload function and records the
reading through ariel_record_reading(); confirmations and the mirrored screen go
through ariel_record_acks() and ariel_set_status() (sgsc-app
database/2026-11-02-ariel-rig-writes.sql). Those functions are all the login may
write, so a stolen Pi can add Ariel readings and nothing else.

Not carried over: the vision prompts (decider "ai", ai_fallback), which Ariel has
not used since 2026-10-18, and the two-prompt rule with its Teams streak, which
only they could reach. A decider other than "yolo" is refused, so settings that
can't be read never turn into an instruction.

Kept apart from loop.py so it can run and be tested without a camera. psycopg2 and
Pillow are imported where they are used: a missing one costs the shadow run, never
the loop.
"""

import datetime
import io
import json
import os
import re
import statistics
import time
from decimal import Decimal

DB_URL = os.environ.get("ARIEL_RIG_DB_URL", "")
DECIDES = os.environ.get("ARIEL_DECIDES", "").strip() == "1"
UPLOAD_URL = os.environ.get("RIG_UPLOAD_URL",
                            "https://pzjsxzgojucqaippctjy.supabase.co/functions/v1/rig-upload")
UPLOAD_TIMEOUT = 30

_conn = None


class Refused(Exception):
    """A reading that can't be analysed, with the status Caliban gives it: loop.py
    treats 409 as an empty chute and 422 as a dark photo."""

    def __init__(self, status, detail):
        super().__init__(detail)
        self.status, self.detail = status, detail


def _db():
    global _conn
    if _conn is None or _conn.closed:
        import psycopg2
        _conn = psycopg2.connect(DB_URL, connect_timeout=5, keepalives=1, keepalives_idle=30,
                                 keepalives_interval=10, keepalives_count=3)
        _conn.autocommit = True
    return _conn


def _query(sql, args=()):
    """Rows as dicts, numbers as floats like the REST API gives them. A failure
    drops the connection, so the next call starts a fresh one."""
    global _conn
    try:
        with _db().cursor() as cur:
            cur.execute(sql, args)
            names = [c.name for c in cur.description]
            return [{n: float(v) if isinstance(v, Decimal) else v for n, v in zip(names, row)}
                    for row in cur.fetchall()]
    except Exception:
        try:
            if _conn is not None:
                _conn.close()
        except Exception:
            pass
        _conn = None
        raise


def config_rows():
    """operator_settings, rig_prompt_variant and inline_settings as text. Through
    a function, because system_config's policies call helpers this login can't."""
    return {r["key"]: r["value"] for r in _query("select key, value from public.ariel_settings()")}


# Columns are named here, never taken from a caller.
READING_COLUMNS = {"estimate_pct", "mesures"}


def recent_readings(column, prompt_version, start, limit):
    assert column in READING_COLUMNS
    return _query(f"""select {column} from public.vision_band_estimates
                      where source = 'inline' and prompt_version = %s
                        and operator_instruction is not null and excluded_reason is null
                        and created_at >= %s
                      order by created_at desc limit %s""", (prompt_version, start, limit))


# --- Copied from Caliban's app/main.py -----------------------------------------

INLINE_READING_ID = re.compile(r"^IL-\d{8}-\d{6}$")


# Over operator_settings(). Every key optional.
#
# mode            'shadow' (default) or 'live'. In shadow the reading is made,
#                 recorded and shown -- but the screen says it is a trial and
#                 offers no instruction to act on, and nothing goes to Teams.
#                 The inline rig runs beside tray sampling until its readings
#                 have been compared with the tray's and the lab's; until then
#                 an instruction from it is a guess an operator might follow.
# interval_min    minutes between readings. Sent back to the Pi with every
#                 reading, so the cadence changes without touching the Pi.
# interval_s      seconds between readings during a correction only: from a
#                 confirmed turn until its first decision (next_reading_s). At
#                 least 20, so the camera and the flash have finished a cycle.
#                 0 = interval_min throughout.
# variant, high_variant, low_variant, repeats, escalate_repeats
#                 as in operator_settings(), for when the chute needs prompts of
#                 its own -- it will: every current prompt describes a dish.
# alert_consecutive_increase
#                 the AUGMENTER run that alerts AQ. Defaults to 3, not the
#                 tray's 2: readings five minutes apart reach two in a row far
#                 more easily than samples half an hour apart.
INLINE_DEFAULTS = {
    "mode": "shadow",
    "interval_min": 5,
    "interval_s": 0,
    "alert_consecutive_increase": 3,
    # How an instruction is decided (inline_decision). With a reading a minute,
    # one photo is too noisy to act on: the instruction comes from the median
    # of the last decision_readings readings, no older than
    # decision_max_age_min and none from before the operator's last
    # confirmation, and needs at least decision_min_readings of them before it
    # asks for anything. 1 / 1 decides on the latest reading alone, as before.
    "decision_readings": 1,
    "decision_min_readings": 1,
    "decision_max_age_min": 30,
    # With the detector deciding: pool every reading of the last
    # decision_window_min minutes (none from before the last confirmation) as
    # one sample -- yolo_scale x 100 x sum(MEO) / sum(product), as a lot is
    # estimated -- instead of the median above. Photos 8 s apart are 70% alike
    # (2026-10-19, 52 bursts), so one photo a minute over ten minutes judges far
    # more of the stream than a burst of 4-8 every few minutes, for the same
    # flashes. decision_min_readings still applies, counted in readings.
    # 0 = the median, as before.
    "decision_window_min": 0,
    # Over a window: no instruction that rests on one photo. Outside the
    # no-change band, the decision is the window without whichever single
    # photo pulls it furthest out (the lowest of the window-less-one values
    # above increase_at, the highest below decrease_below). On 2026-10-23..26
    # one photo alone made 39 of 119 AUGMENTER and 26 of 102 DIMINUER -- one
    # of 84.6% turned a 1.2% window into a 12% AUGMENTER major. A real
    # build-up is in most of the photos and still shows the same minute.
    "decision_drop_one": False,
    # After a confirmed turn of the handle, the first settle_min minutes of
    # photos are left out of the decision: the product in the chute then is
    # still mostly what went through before the turn (2026-10-20: 10.7%
    # before a 4 cm turn, 18.6% in the next 5 minutes, 6.9% in the 5 after).
    # The next instruction then comes from the decision_min_readings photos
    # after that -- about ten minutes after the turn. 0 = judge from the turn on.
    "settle_min": 0,
    # Turns sized from what the last one did (inline_turn). Off: the screen's
    # fixed 2 cm / 4 cm. On: a first AUGMENTER keeps those; another one right
    # after a confirmed turn is (ME% - target) / (ME% moved per cm by that
    # turn), in whole cm, from TURN_MIN_CM to turn_max_cm -- never above
    # TURN_HARD_MAX_CM whatever the settings say. turn_gain_per_cm is the
    # effect expected (about 1 point per cm over 10 confirmed turns,
    # 2026-10-20..23), and bounds the measured one to half..double of it.
    "adaptive_turns": False,
    "turn_gain_per_cm": 1.0,
    "turn_max_cm": 6,
    # Anticlockwise cm over consecutive AUGMENTER turns before the screen
    # stops asking for more and asks for AQ instead: the handle has a physical
    # end, and past the gate's closed point a turn reverses its effect.
    "turn_run_max_cm": 12,
    # The same in quarter turns of the crank (turn_unit "quarter"): at most
    # one full turn at a time, two in a row. turn_gain_per_quarter is the
    # ME% a quarter turn is expected to move; 0 until measured, which keeps
    # the fixed quarter turns (adaptive sizing off, its safety stops still on).
    "turn_unit": "cm",
    "turn_max_qt": 4,
    "turn_run_max_qt": 8,
    "turn_gain_per_quarter": 0.0,
    # DIMINUER does not show in ME% (about -0.1 point per cm over 27 turns),
    # so nothing confirms it worked and it used to come back every five
    # minutes (18 in a row overnight 2026-10-22, ~36 cm). After one, none for
    # decrease_pause_min; after decrease_max_in_row in a row, none until ME%
    # has been back at decrease_below or above. 0 = no limit.
    "decrease_pause_min": 0,
    "decrease_max_in_row": 0,
    # Photos per reading. Above 1 the Pi takes a burst of group_size photos,
    # group_spacing_s apart, and sends them together for a group prompt
    # (chute-g1) to judge as one tray-sized sample -- see rig/prompt_chute_g1.sql.
    "group_size": 1,
    "group_spacing_s": 12,
    # Flow-sized bursts. A fixed burst holds more product when the flow is
    # heavy, and a group prompt reads a busier sample as a dirtier one
    # (2026-10-17: clean product read 19% ME at full flow). With
    # group_target_cover set, the Pi keeps shooting after group_size photos
    # until the product coverage of the burst, summed over its photos (each
    # 0-1, measured against the empty chute), reaches the target -- up to
    # group_max photos. Every reading then judges about the same amount of
    # product, light flow or heavy. 0 = a fixed burst of group_size.
    "group_max": 0,
    "group_target_cover": 0.0,
    # Who reads the burst. "ai": the vision prompts (variant above), as
    # before. "yolo": the MEO detector on Ariel (rig/inline/meo_detect.py,
    # trained on the boxed tiles in training/amas/), which sends each photo's
    # MEO box area and product pixels; the estimate is yolo_scale x 100 x
    # sum(MEO) / sum(product), on the lab ME% scale (yolo_scale fitted on lab
    # lots: 3.19 on 2026-10-18), and no vision call is made. Without the
    # detector's numbers (Ariel missing onnxruntime or the model) the reading
    # goes to the prompts only if ai_fallback; otherwise it is refused (503).
    "decider": "ai",
    "yolo_scale": 3.19,
    "ai_fallback": False,
    # The detector's numbers without the dust stuck to the chute (Ariel's
    # meo_detect.Background: boxes on, and product pixels of, whatever stays
    # put over the last few photos). Ariel sends them next to the raw ones
    # once it has five photos of background; every reading keeps both
    # (measures.yolo.clean / .raw), and this says which the estimate uses.
    # The raw ones are what yolo_scale was fitted on: refit it on the clean
    # ones before turning this on.
    "dust_mask": False,
    # The detector's numbers without the strip below the chute's lip where
    # dust piles up (meo_detect.STRIP; Ariel sends them as "strip" from
    # 2026-10-26). Takes precedence over dust_mask. Its own scale: lot_estimate
    # fits it as yolo_scale_strip. The readings' prompt_version gets "+strip",
    # so a window never pools them with readings measured the other way.
    "exclude_strip": False,
}


# How an operator capture is analysed when system_config says nothing.
#
# repeats           rotations on every capture.
# escalate_repeats  rotations in total once any of the first ones reads at or
#                   above increase_at. Rotations are spent where a decision is
#                   about to be made: most captures are conform and stop at the
#                   base count, so the extra cost lands only on readings an
#                   operator will act on.
# increase_at       ME% at or above which the destoner goes up. The 8% line --
#                   the conformity boundary, and the one the model has never
#                   been validated against, which is why it gets the rotations.
#                   Below increase_medium_at, AUGMENTER shows as "minor".
# increase_medium_at  ME% at or above which AUGMENTER shows as "medium"
#                   rather than "minor" -- the coarse6 8-10%/10-13% line. Below
#                   alert_at still, so this is severity wording only; it does
#                   not change whether AQ is told (that's alert_at, and the
#                   consecutive-AUGMENTER streak counts a minor/medium/major
#                   reading the same either way).
# decrease_below    ME% below which the destoner comes down, to stop discarding
#                   good larvae along with the frass.
# alert_at          ME% at or above which AQ is told as well, and above which
#                   AUGMENTER shows as "major". A reading that far out is a
#                   non-conformance, not a knob to turn alone.
# sample_interval_min  minutes between samples; the operator screen counts down
#                   from the last analysed sample and alerts when one is due.
#                   0 turns the countdown off.
# followup_interval_min  minutes to the check sample after an AUGMENTER or
#                   DIMINUER: make the change, wait, resample, so every
#                   correction is verified instead of made and walked away
#                   from. 0 means no check; the normal interval applies.
OPERATOR_DEFAULTS = {
    "repeats": 2,
    "escalate_repeats": 6,
    "increase_at": 8.0,
    "increase_medium_at": 10.0,
    "decrease_below": 3.0,
    "alert_at": 13.0,
    "sample_interval_min": 30,
    "followup_interval_min": 5,
    # Two-prompt rule (see operator_prompt_pair). Empty = one prompt, as before.
    "high_variant": "",
    "low_variant": "",
    # Tell AQ when this many AUGMENTER decisions come in a row within a
    # production cycle (increase_streak_alert). 0 = off.
    "alert_consecutive_increase": 2,
}


# Band labels are categorical, not numeric -- outlier-flagging and any
# future "how far off were we" math needs a number to compare against
# real_me_pct, so this maps each band to its midpoint. ">14%" has no
# real upper bound; 17 is an arbitrary anchor (a modest extrapolation
# past the 10-14% midpoint of 12), not a measured value -- only used to
# decide "is this worth a human look", never displayed as a real number.
# A band scale is a property of the prompt that produced it, not of the
# service. The prompt lists the permitted labels, so changing the scale means
# changing the prompt -- and rows recorded under one scale can never be
# compared with rows recorded under another as bands. They can still be
# compared as estimate_pct, which is the reason that column exists.
#
# Each entry is (label, lower_bound, midpoint). Bounds are lower-inclusive and
# read in order; the last entry is open-ended.
BAND_SCALES = {
    # The original six. Fine-grained, and finer than the model can reliably
    # resolve -- the 3-7 / 7-10 confusion has been the standing complaint.
    "standard": [
        ("<1%", 0.0, 0.5),
        ("1-3%", 1.0, 2.0),
        ("3-7%", 3.0, 5.0),
        ("7-10%", 7.0, 8.5),
        ("10-14%", 10.0, 12.0),
        (">14%", 14.0, 17.0),
    ],
    # Four wider bands for internal use. The trade is deliberate: fewer, wider
    # buckets are a question the model can actually answer, and the resolution
    # given up is recovered by averaging rotations into estimate_pct, which is
    # continuous and finer than any band. Before that existed, coarsening would
    # have been a pure loss.
    "coarse": [
        ("<3%", 0.0, 1.5),
        ("3-8%", 3.0, 5.5),
        ("8-13%", 8.0, 10.5),
        (">13%", 13.0, 16.0),
    ],
    # Six bands in steps of roughly 3, replacing "coarse" for the live
    # operator prompts (3.2b, 3.4b -- see rig/prompt_3_2b_bands6.sql and
    # rig/prompt_3_4b_bands6.sql). "coarse" widened 3-8/8-13 into single
    # buckets specifically to give the model a question it could answer
    # consistently; this narrows the two ends the operator screen actually
    # acts on (below 3, above 8) back down without reopening the original
    # 3-7/7-10 confusion the four-band scale was built to fix in the first
    # place -- 8-10/10-13 exist so a later AQ severity tier (minor/medium
    # at those two, major above 13) has bands to key off, but that tiering
    # is operator-screen (SGSC) work, not decided by this scale on its own.
    "coarse6": [
        ("<3%", 0.0, 1.5),
        ("3-5%", 3.0, 4.0),
        ("5-8%", 5.0, 6.5),
        ("8-10%", 8.0, 9.0),
        ("10-13%", 10.0, 11.5),
        (">13%", 13.0, 16.0),
    ],
}


# REVERTED to 1.5 on 2026-08-23. The dish method was abandoned: it proved less
# accurate than scattering onto a tray, which is what the rig now photographs.
# So 2.0's premise -- a circular dish filled and levelled to a flat surface --
# describes an arrangement that no longer exists, and running it against a tray
# photo is the same false-premise mistake, just pointed the other way.
#
# 1.5 was the best performer on scatter and is performing well again. Its known
# weakness is the 3-7% / 7-10% boundary, which may be the reference photos
# rather than the prompt -- they are due to be re-shot on the new rig.
#
# A 3.0 written for the blue tray under even LED lighting is the intended
# successor, once there are frames from that setup to write it against.
DEFAULT_PROMPT_VARIANT = "1.5"


# The instruction must always agree with the band the operator types into
# Ignition. DIMINUER below 4.0 once came with a 3-8% band -- two <3% readings and
# two 3-8% average 3.5, which is labelled 3-8% -- and "3-8% but reduce" read as
# a contradiction on the line. So whatever AQ sets, DIMINUER is only possible
# where the band is <3% and AUGMENTER only where it is 8-10% or above: the
# lower bounds of coarse6's second and fourth bands, 3.0 and 8.0 -- unchanged
# from the old coarse-scale values, since 3.2b/3.4b moving to coarse6 only
# split the middle into finer bands, it did not move the <3/8+ boundary
# lines this screen actually decides on.
OP_DECREASE_MAX = BAND_SCALES["coarse6"][1][1]


OP_INCREASE_MIN = BAND_SCALES["coarse6"][3][1]


# A photo the rig could not really take -- the light off, the lens blocked, the
# camera knocked out of its mount (2026-09-15: operators moved the rig and the
# camera popped out; every capture after that was black). The model answers a
# black frame as confidently as a tray, so the frame is judged before any call
# is made: there is no band in a photo with nothing in it.
BLACK_MEAN_MAX = 20.0      # of 255; a lit tray sits well above 100


FLAT_STD_MAX = 6.0         # near-uniform frame: nothing to see


DARK_MEAN_MAX = 60.0       # ... and dark with it


BLOWN_MEAN_MIN = 245.0     # flooded with light


# Longest edge sent to the vision model. GPT-4o scales anything larger to fit
# 2048x2048 before tiling, so this is the point past which extra pixels are
# discarded rather than used -- resizing here is free in accuracy and saves the
# memory, bandwidth and latency of carrying them to Azure to be thrown away.
MODEL_MAX_EDGE = 2048


# JPEG quality for the review copy kept in Supabase Storage.
STORED_CAPTURE_QUALITY = 90


def band_for_pct(pct, scale="standard"):
    """The band a percentage falls in, on a given scale.

    Derived from the boundaries rather than by picking the nearest midpoint,
    because the midpoints are not evenly spaced and nearest-midpoint would put
    7.4% in "7-10%" while 6.9% landed in "3-7%" correctly but 7.6% did not.

    The scale has to be passed in: a mean of 5.0 is "3-7%" on the standard
    scale and "3-8%" on the coarse one, and guessing would silently mislabel
    every averaged estimate from whichever variant was not the default.
    """
    if pct is None:
        return None
    bands = BAND_SCALES[scale]
    label = bands[0][0]
    for candidate, lower, _mid in bands:
        if pct >= lower:
            label = candidate
    return label


def photo_stats(img):
    """(mean, std) brightness of a photo, 0-255, on a small grey copy.

    The mean is how bright the frame is, the standard deviation how much there
    is to see in it. Kept with every estimate (rig/photo_light.sql): exposure
    and gain are fixed at calibration, so if the bench light is turned down
    afterwards every photo darkens -- and a darker tray reads as more MEO.
    Without this recorded, that is indistinguishable from real contamination.
    """
    from PIL import ImageStat

    stat = ImageStat.Stat(img.convert("L").resize((64, 64)))
    return round(stat.mean[0], 1), round(stat.stddev[0], 1)


def unusable_photo(img):
    """Why this photo cannot be judged, or None if it looks like a real tray."""
    mean, std = photo_stats(img)
    if mean <= BLACK_MEAN_MAX:
        return (f"Photo noire (luminosité moyenne {mean:.0f}/255) -- éclairage éteint, "
                f"objectif obstrué ou caméra déplacée. Prévenir l'AQ.")
    if std <= FLAT_STD_MAX and mean <= DARK_MEAN_MAX:
        return (f"Photo vide et sombre (luminosité {mean:.0f}/255, variation {std:.1f}) -- "
                f"la caméra ne voit pas le plateau. Prévenir l'AQ.")
    if mean >= BLOWN_MEAN_MIN and std <= FLAT_STD_MAX:
        return (f"Photo surexposée (luminosité {mean:.0f}/255, variation {std:.1f}) -- "
                f"éclairage ou exposition à vérifier. Prévenir l'AQ.")
    return None


def review_copy(contents):
    """The copy of a capture kept in Supabase Storage: MODEL_MAX_EDGE on the
    long side, re-encoded at STORED_CAPTURE_QUALITY.

    The rig's 12MP originals were 3-5 MB each and put the project over the
    free tier's 1 GB Storage Size and most of its 5 GB egress (2026-09-27),
    while the model never sees past MODEL_MAX_EDGE anyway. Supabase holds a
    few days of these for review; the full-resolution original stays on the
    rig in ~/captures, named in capture_commands.original_filename. Anything
    that is not a readable image is kept as it came rather than failing the
    capture over it."""
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(contents)).convert("RGB")
    except Exception as e:
        print(f"[review copy skipped, stored as received] {type(e).__name__}: {e}")
        return contents
    if max(img.size) > MODEL_MAX_EDGE:
        img.thumbnail((MODEL_MAX_EDGE, MODEL_MAX_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=STORED_CAPTURE_QUALITY)
    return buf.getvalue()


def operator_settings():
    """Operator analysis settings: the code defaults, overridden key by key by
    system_config.operator_settings (a JSON object), with the prompt taken from
    the same rig_prompt_variant AQ already sets for QC captures -- one method,
    not two drifting apart.

    Read fresh per capture, like rigConfig() in the browser, so a change AQ
    makes reaches the next press rather than the next restart. Every failure
    degrades to the defaults instead of refusing: an operator standing at the
    line with a tray should get an answer, and the defaults are sane.
    """
    settings = dict(OPERATOR_DEFAULTS)
    settings["variant"] = DEFAULT_PROMPT_VARIANT
    try:
        rows = config_rows()
    except Exception as e:
        print(f"[operator_settings lookup failed] {type(e).__name__}: {e}")
        return settings

    variant = rows.get("rig_prompt_variant")
    if isinstance(variant, str) and variant.strip():
        settings["variant"] = variant.strip()

    # system_config.value is text; the JSON inside it is parsed here.
    overrides = rows.get("operator_settings")
    if isinstance(overrides, str):
        try:
            overrides = json.loads(overrides)
        except ValueError:
            overrides = None
    if isinstance(overrides, dict):
        for key, default in OPERATOR_DEFAULTS.items():
            try:
                settings[key] = type(default)(overrides[key])
            except (KeyError, TypeError, ValueError):
                pass

    settings["decrease_below"] = min(settings["decrease_below"], OP_DECREASE_MAX)
    settings["increase_at"] = max(settings["increase_at"], OP_INCREASE_MIN)
    settings["increase_medium_at"] = max(settings["increase_medium_at"], settings["increase_at"])
    settings["alert_at"] = max(settings["alert_at"], settings["increase_medium_at"])

    settings["repeats"] = max(1, min(8, settings["repeats"]))
    settings["escalate_repeats"] = max(settings["repeats"], min(8, settings["escalate_repeats"]))
    settings["sample_interval_min"] = max(0, min(240, settings["sample_interval_min"]))
    settings["followup_interval_min"] = max(0, min(60, settings["followup_interval_min"]))
    settings["alert_consecutive_increase"] = max(0, min(10, settings["alert_consecutive_increase"]))
    settings["high_variant"] = settings["high_variant"].strip()
    settings["low_variant"] = settings["low_variant"].strip()
    return settings


def increase_tier(estimate_pct, settings):
    """'increase_minor' / 'increase_medium' / 'increase_major' for an estimate
    already known to be >= increase_at. Severity wording only -- does not
    decide the alert flag (alert_at does that, independently) or the
    consecutive-AUGMENTER streak (which counts any tier the same)."""
    if estimate_pct >= settings["alert_at"]:
        return "increase_major"
    if estimate_pct >= settings["increase_medium_at"]:
        return "increase_medium"
    return "increase_minor"


def operator_instruction(estimate_pct, settings):
    """What the operator should do to the destoner, and whether AQ must hear
    about it too. Returns (instruction, alert).

    Decided from estimate_pct rather than the band label, so it holds whatever
    band scale the configured prompt speaks: the thresholds are ME% values, and
    a label is only a name for a range of them.
    """
    if estimate_pct is None:
        return None, False
    if estimate_pct >= settings["increase_at"]:
        return increase_tier(estimate_pct, settings), estimate_pct >= settings["alert_at"]
    if estimate_pct < settings["decrease_below"]:
        return "decrease", False
    return "hold", False


def inline_settings():
    """operator_settings() with system_config.inline_settings laid over it.
    Read fresh per reading; any failure keeps the defaults."""
    settings = operator_settings()
    settings.update(INLINE_DEFAULTS)
    try:
        raw = config_rows().get("inline_settings")
        overrides = json.loads(raw) if isinstance(raw, str) else raw
    except Exception as e:
        print(f"[inline_settings lookup failed] {type(e).__name__}: {e}")
        overrides = None
    if isinstance(overrides, dict):
        for key in ("variant", "high_variant", "low_variant", "mode", "decider", "turn_unit"):
            if isinstance(overrides.get(key), str):
                settings[key] = overrides[key].strip()
        for key in ("repeats", "escalate_repeats", "interval_min", "alert_consecutive_increase",
                    "decision_readings", "decision_min_readings", "decision_max_age_min",
                    "decision_window_min", "group_size", "group_spacing_s", "group_max",
                    "settle_min", "turn_max_cm", "turn_run_max_cm", "decrease_pause_min",
                    "decrease_max_in_row", "turn_max_qt", "turn_run_max_qt", "interval_s"):
            try:
                settings[key] = int(overrides[key])
            except (KeyError, TypeError, ValueError):
                pass
        # The inline rig's own thresholds. Its readings are on their own scale
        # (chute prompts, uncalibrated: about half the lab ME% on 2026-10-16),
        # so the tray's limits -- and the clamps operator_settings puts on
        # them -- do not carry over. Only their order is enforced, below.
        for key in ("increase_at", "increase_medium_at", "decrease_below", "alert_at", "group_target_cover",
                    "yolo_scale", "turn_gain_per_cm", "turn_gain_per_quarter"):
            try:
                settings[key] = float(overrides[key])
            except (KeyError, TypeError, ValueError):
                pass
        for key in ("offline_alert", "teams", "ai_fallback", "adaptive_turns", "dust_mask", "decision_drop_one",
                    "exclude_strip"):
            if isinstance(overrides.get(key), bool):
                settings[key] = overrides[key]
        # A straight line from the prompt's value to lab ME%, fitted on lots
        # with lab results ({"slope": 6.11, "intercept": -20.75} for chute-g1
        # on 2026-10-16). With it, thresholds, the decision and the screen are
        # all in approximate lab ME%; the raw value is still what is recorded
        # in estimate_pct, so the line can be refitted on any day's readings.
        cal = overrides.get("calibration")
        if isinstance(cal, dict):
            try:
                settings["calibration"] = {"slope": float(cal["slope"]), "intercept": float(cal["intercept"])}
            except (KeyError, TypeError, ValueError):
                pass
    if settings["mode"] not in ("shadow", "live"):
        settings["mode"] = "shadow"
    if settings["decider"] not in ("ai", "yolo"):
        settings["decider"] = "ai"
    settings["yolo_scale"] = max(0.1, min(50.0, settings["yolo_scale"]))
    # Teams follows live mode unless told otherwise: live with teams=false is
    # the operator acting on the screen while AQ watches the line in person.
    settings.setdefault("teams", settings["mode"] == "live")
    settings["decrease_below"] = max(0.0, settings["decrease_below"])
    settings["increase_at"] = max(settings["increase_at"], settings["decrease_below"])
    settings["increase_medium_at"] = max(settings["increase_medium_at"], settings["increase_at"])
    settings["alert_at"] = max(settings["alert_at"], settings["increase_medium_at"])
    settings["decision_readings"] = max(1, min(30, settings["decision_readings"]))
    settings["decision_window_min"] = max(0, min(60, settings["decision_window_min"]))
    # Over a window, the minimum is a count of readings in it, not of a median's.
    cap = 60 if settings["decision_window_min"] else settings["decision_readings"]
    settings["decision_min_readings"] = max(1, min(cap, settings["decision_min_readings"]))
    settings["decision_max_age_min"] = max(5, min(240, settings["decision_max_age_min"]))
    settings["settle_min"] = max(0, min(30, settings["settle_min"]))
    settings["turn_gain_per_cm"] = max(0.25, min(4.0, settings["turn_gain_per_cm"]))
    settings["turn_max_cm"] = max(TURN_MIN_CM, min(TURN_HARD_MAX_CM, settings["turn_max_cm"]))
    settings["turn_run_max_cm"] = max(settings["turn_max_cm"], min(40, settings["turn_run_max_cm"]))
    if settings["turn_unit"] not in TURN_UNITS:
        settings["turn_unit"] = "cm"
    qt = TURN_UNITS["quarter"]
    settings["turn_max_qt"] = max(qt["min"], min(qt["hard_max"], settings["turn_max_qt"]))
    settings["turn_run_max_qt"] = max(settings["turn_max_qt"], min(16, settings["turn_run_max_qt"]))
    settings["turn_gain_per_quarter"] = max(0.0, min(4.0, settings["turn_gain_per_quarter"]))
    settings["decrease_pause_min"] = max(0, min(120, settings["decrease_pause_min"]))
    settings["decrease_max_in_row"] = max(0, min(10, settings["decrease_max_in_row"]))
    settings["group_size"] = max(1, min(8, settings["group_size"]))
    settings["group_spacing_s"] = max(3, min(60, settings["group_spacing_s"]))
    settings["group_max"] = max(settings["group_size"], min(8, settings["group_max"]))
    settings["group_target_cover"] = max(0.0, min(8.0, settings["group_target_cover"]))
    settings["repeats"] = max(1, min(8, settings["repeats"]))
    settings["escalate_repeats"] = max(settings["repeats"], min(8, settings["escalate_repeats"]))
    settings["interval_min"] = max(1, min(60, settings["interval_min"]))
    settings["interval_s"] = max(20, min(3600, settings["interval_s"])) if settings["interval_s"] > 0 else 0
    settings["alert_consecutive_increase"] = max(0, min(10, settings["alert_consecutive_increase"]))
    return settings


# Handle turns. turn_unit says what the screen counts them in: "cm" round the
# handle, or "quarter" turns of the crank (from 2026-10-24: a quarter turn is
# the same for everyone, cm along the rim was not). Whatever the settings or
# the arithmetic say, a turn on the screen is a whole number of units between
# the unit's min and hard max (Tim, 2026-10-23: never more than 10 cm, never a
# fraction; 2026-10-24: never more than one full turn at a time).
TURN_UNITS = {
    "cm":      {"min": 2, "hard_max": 10,
                "ladder": {"increase_minor": 2, "increase": 2, "increase_medium": 4, "increase_major": 4,
                           "decrease": 2}},
    "quarter": {"min": 1, "hard_max": 4,
                "ladder": {"increase_minor": 1, "increase": 1, "increase_medium": 2, "increase_major": 2,
                           "decrease": 1}},
}


TURN_MIN_CM = TURN_UNITS["cm"]["min"]


TURN_HARD_MAX_CM = TURN_UNITS["cm"]["hard_max"]


# ME% points higher after an AUGMENTER turn than before it that stop the
# turns (inline_turn): more than the pooled 10 minutes wander on their own.
TURN_WRONG_WAY_PTS = 2.0


def safe_turn(n, unit="cm", cap=None):
    """n rounded to a whole number of the unit within its min..min(cap, hard
    max), or None when it is not a usable number at all."""
    u = TURN_UNITS.get(unit, TURN_UNITS["cm"])
    try:
        n = float(n)
    except (TypeError, ValueError):
        return None
    if n != n or abs(n) == float("inf"):
        return None
    top = u["hard_max"] if cap is None else min(cap, u["hard_max"])
    return int(max(u["min"], min(top, round(n))))


def safe_turn_cm(cm, cap=TURN_HARD_MAX_CM):
    return safe_turn(cm, "cm", cap)


def turn_limits(settings):
    """(unit, largest turn, most in a row, ME% moved per unit expected -- 0
    when not known, which keeps the fixed turns) for settings' turn_unit."""
    if settings["turn_unit"] == "quarter":
        return "quarter", settings["turn_max_qt"], settings["turn_run_max_qt"], settings["turn_gain_per_quarter"]
    return "cm", settings["turn_max_cm"], settings["turn_run_max_cm"], settings["turn_gain_per_cm"]


def inline_turn(instruction, decision_pct, settings, ctx):
    """(instruction, turn, basis, alert_reason): how far to turn for this
    instruction, in whole units of settings' turn_unit. ctx is what Ariel says
    about the confirmations before (inline_ack_context), in the same unit.
    basis: 'ladder' (the fixed turns), 'adaptive' (sized from the last turn),
    'limit' (no more turning, AQ instead: the run of turns reached its most,
    or ME% rose after the last one), and for a DIMINUER held back
    'decrease_pause' / 'decrease_limit', with the instruction turned into
    hold. alert_reason is set only for 'limit'."""
    unit, most, run_most, expected = turn_limits(settings)
    ladder = TURN_UNITS[unit]["ladder"]
    if instruction == "decrease":
        run = ctx.get("decrease_run") or 0
        if settings["decrease_max_in_row"] and run >= settings["decrease_max_in_row"]:
            return "hold", None, "decrease_limit", None
        since = ctx.get("since_ack_s")
        if (settings["decrease_pause_min"] and run >= 1 and since is not None
                and since < settings["decrease_pause_min"] * 60):
            return "hold", None, "decrease_pause", None
        return instruction, ladder["decrease"], "ladder", None
    if not (instruction or "").startswith("increase"):
        return instruction, None, None, None

    n, basis = ladder.get(instruction, TURN_UNITS[unit]["min"]), "ladder"
    before, last = ctx.get("last_decision_pct"), ctx.get(f"last_turn_{unit}")
    if (settings["adaptive_turns"] and decision_pct is not None
            and (ctx.get("last_instruction") or "").startswith("increase")
            and ctx.get("last_choice") in ("adjusted", "set")
            and before is not None and last and last > 0):
        # Clearly worse after turning the right way: the gate may be past its
        # closed point, where a turn does the opposite -- no more turns, AQ.
        if decision_pct - before >= TURN_WRONG_WAY_PTS:
            return instruction, None, "limit", "turn_wrong_way"
        if expected > 0:
            # ME% moved per unit by the last turn, held to half..double the
            # effect expected: a turn that did little or went the wrong way
            # makes the next one larger, never smaller, than the plain estimate.
            gain = min(2 * expected, max(0.5 * expected, (before - decision_pct) / last))
            target = (settings["decrease_below"] + settings["increase_at"]) / 2
            sized = safe_turn((decision_pct - target) / gain, unit, most)
            if sized is not None:
                n, basis = sized, "adaptive"
    room = run_most - (ctx.get(f"increase_run_{unit}") or 0)
    if room < TURN_UNITS[unit]["min"]:
        return instruction, None, "limit", "turn_limit"
    return instruction, safe_turn(min(n, room), unit, most), basis, None


def next_reading_s(settings, correcting):
    """Seconds to the next reading: interval_s while a turn's first decision is
    still being gathered, so the operator is not left waiting, and interval_min
    the rest of the time, so the flash does not fire twice as often all day."""
    if correcting and settings["interval_s"]:
        return settings["interval_s"]
    return settings["interval_min"] * 60


def inline_ack_context(since_ack_s, last_ack_instruction, fields):
    """What the turn rules need about earlier confirmations, from Ariel's form
    fields, the turns in cm and in quarter turns. All optional: an older
    loop.py sends none of them and gets the fixed turns with no limits."""
    def num(key, cast=float):
        try:
            v = cast(str(fields.get(key) or "").strip())
        except (TypeError, ValueError):
            return None
        return v if v == v and abs(v) != float("inf") else None
    return {
        "since_ack_s": since_ack_s,
        "last_instruction": last_ack_instruction,
        "last_choice": str(fields.get("last_ack_choice") or "").strip() or None,
        "last_decision_pct": num("last_ack_decision_pct"),
        "last_turn_cm": num("last_ack_turn_cm"),
        "last_turn_quarter": num("last_ack_turn_qt"),
        "increase_run_cm": max(0, num("increase_run_cm") or 0),
        "increase_run_quarter": max(0, num("increase_run_qt") or 0),
        "decrease_run": max(0, num("decrease_run", int) or 0),
    }


def inline_decision(estimate_pct, prompt_version, settings, since_ack_s=None):
    """(decision_pct, n) -- the value the instruction is decided on.

    The median of this reading and the ones just before it from the same
    prompt: at most decision_readings of them, none older than
    decision_max_age_min, and none taken before the operator last confirmed an
    instruction (since_ack_s, measured on the Pi's own clock so a clock that
    is off does not matter). After a confirmation the destoner has been
    changed, and the readings from before describe a setting that no longer
    exists; counting them would ask for the same change again straight away.

    n is how many readings the median is over. Fewer than
    decision_min_readings means "too soon to say" -- the caller holds.
    A lookup failure falls back to this reading alone.
    """
    if estimate_pct is None:
        return None, 0
    values = [float(estimate_pct)]
    want = settings["decision_readings"] - 1
    if want > 0:
        horizon = settings["decision_max_age_min"] * 60
        if since_ack_s is not None:
            horizon = min(horizon, max(0, since_ack_s))
        start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=horizon)
        try:
            rows = recent_readings("estimate_pct", prompt_version, start, want)
            values += [float(r["estimate_pct"]) for r in rows if r.get("estimate_pct") is not None]
        except Exception as e:
            print(f"[inline decision lookup failed] {type(e).__name__}: {e}")
    return round(statistics.median(values), 2), len(values)


def settling_s(since_ack_s, settings, turned):
    """Seconds left before the photos after a confirmed turn count (settle_min),
    0 when they already do or nothing was turned."""
    if not turned or since_ack_s is None or not settings["settle_min"]:
        return 0
    return max(0, int(settings["settle_min"] * 60 - since_ack_s))


def inline_window_decision(yolo, prompt_version, settings, since_ack_s=None, turned=False):
    """(decision_pct, n) for the detector: this reading and every one from the
    same model in the last decision_window_min minutes (none from before the
    operator's last confirmation, as in inline_decision), pooled as one sample
    -- yolo_scale x 100 x sum(MEO px) / sum(product px). n is the number of
    readings pooled. A lookup failure falls back to this reading alone.
    turned: that confirmation was a turn of the handle; for its first
    settle_min minutes there is no decision yet ((None, 0): still settling),
    and after them only the readings from then on count.
    With decision_drop_one, a pooled value outside the no-change band is
    replaced by the window without its most influential photo
    (window_drop_one). Returns (decision_pct, n, pooled_pct): the last is
    the plain pooled value, the same as decision_pct without the rule."""
    photos = [(float(yolo["meo_px"]), float(yolo["product_px"]))]
    horizon = settings["decision_window_min"] * 60
    if since_ack_s is not None:
        horizon = min(horizon, max(0, since_ack_s))
    if settling_s(since_ack_s, settings, turned):
        return None, 0, None
    if turned and since_ack_s is not None:
        horizon = min(horizon, max(0, since_ack_s - settings["settle_min"] * 60))
    if horizon > 0:
        start = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(seconds=horizon)
        try:
            rows = recent_readings("mesures", prompt_version, start, 200)
            for r in rows:
                y = (r.get("mesures") or {}).get("yolo") or {}
                if y.get("product_px"):
                    photos.append((float(y.get("meo_px") or 0), float(y["product_px"])))
        except Exception as e:
            print(f"[inline window lookup failed] {type(e).__name__}: {e}")
    pooled = pooled_pct(photos, settings)
    if settings["decision_drop_one"]:
        return window_drop_one(photos, pooled, settings), len(photos), pooled
    return pooled, len(photos), pooled


def pooled_pct(photos, settings):
    """yolo_scale x 100 x sum(MEO px) / sum(product px) over (meo_px,
    product_px) pairs."""
    return round(settings["yolo_scale"] * 100 * sum(m for m, _ in photos) / sum(p for _, p in photos), 2)


def window_drop_one(photos, pooled, settings):
    """The decision over a window that no single photo decides: pooled
    unchanged inside the no-change band; above increase_at, the lowest
    value the window takes with one photo left out; below decrease_below,
    the highest. So AUGMENTER (DIMINUER) shows only when every photo but
    one still says so, and the tier and turn come from that value too.
    Never past the band's far side: a 12% window that is 1.2% without its
    one wild photo is no change at decrease_below, not a DIMINUER."""
    if len(photos) < 2 or settings["decrease_below"] <= pooled < settings["increase_at"]:
        return pooled
    meo, product = sum(m for m, _ in photos), sum(p for _, p in photos)
    without = [pooled_pct([(meo - m, product - p)], settings) for m, p in photos]
    if pooled >= settings["increase_at"]:
        return max(min(without), settings["decrease_below"])
    value = max(without)
    return settings["decrease_below"] if value >= settings["increase_at"] else value


def inline_calibrated(value, settings):
    """value on the lab ME% scale through inline_settings.calibration, or
    unchanged without one. Never below 0: a line fitted on a handful of lots
    runs negative below its lowest lot, and a negative ME% means nothing."""
    cal = settings.get("calibration")
    if value is None or not cal:
        return value
    return round(max(0.0, cal["slope"] * float(value) + cal["intercept"]), 2)


# Fewer product pixels per photo than this and the detector's ME% is refused
# (409): it divides by them, and an empty chute with a few false boxes read
# 3909 % on 2026-10-23 (39 px). Running flow is 240k-380k px; ~1 % of the
# crop. The rig skips such photos itself (flashcam min_product); this is the
# backstop for a rig running older code.
YOLO_MIN_PRODUCT_PX = 10_000


def inline_detector(raw, n_photos):
    """Ariel's MEO detector numbers for a burst (the detecteur form field:
    {"version", "photos": [{"meo_px", "meo_n", "product_px", "ms"}, ...]}),
    checked, or None when absent or unusable -- one entry per photo, product
    seen on at least one. Each photo's MEO boxes ([x0, y0, x1, y1, score] as
    fractions of the photo) and the measured crop are kept when Ariel sends
    them, for SGSC to draw on the photo; anything malformed there is dropped,
    never the reading."""
    if not (raw or "").strip():
        return None
    try:
        det = json.loads(raw)
        photos = det["photos"]
        if not isinstance(photos, list) or len(photos) != n_photos:
            return None
        rows = [{"meo_px": max(0.0, float(ph["meo_px"])), "meo_n": max(0, int(ph["meo_n"])),
                 "product_px": max(0, int(ph["product_px"])), "ms": int(ph.get("ms") or 0)} for ph in photos]
    except (ValueError, TypeError, KeyError):
        return None
    if sum(r["product_px"] for r in rows) <= 0:
        return None
    for row, ph in zip(rows, photos):
        boxes = _fractions(ph.get("boxes"), 5, 200)
        if boxes is not None:
            row["boxes"] = boxes
        # Without the dust (Ariel's meo_detect.Background), once it has a
        # background, and without the dust strip (meo_detect.STRIP); a
        # malformed one is dropped, never the reading.
        for key in ("clean", "strip"):
            try:
                c = ph[key]
                row[key] = {"meo_px": max(0.0, float(c["meo_px"])), "meo_n": max(0, int(c["meo_n"])),
                            "product_px": max(0, int(c["product_px"]))}
            except (KeyError, TypeError, ValueError):
                pass
        # MEO and product as surface (meo_detect.AREA_WARM), with and
        # without the strip: recorded for the area ME% under study, never
        # used to decide.
        for key in ("area", "area_strip"):
            try:
                c = ph[key]
                row[key] = {"meo_px": max(0, int(c["meo_px"])), "product_px": max(0, int(c["product_px"]))}
            except (KeyError, TypeError, ValueError):
                pass
        # Bicolore (dark or two-tone larvae, meo_detect.BICOLORE): count,
        # covered area and boxes, for SGSC to show on the photo and per lot.
        # Shown only, never used to decide.
        try:
            c = ph["bicolore"]
            bi = {"n": max(0, int(c["n"])), "px": max(0, int(c["px"]))}
            bi_boxes = _fractions(c.get("boxes"), 5, 200)
            if bi_boxes is not None:
                bi["boxes"] = bi_boxes
            row["bicolore"] = bi
        except (KeyError, TypeError, ValueError):
            pass
        # The isolated larvae's length (meo_detect.larva_length), which SGSC
        # turns into the lot's density estimate. Never used to decide here.
        try:
            c = ph["larve"]
            length = c.get("len_p75")
            row["larve"] = {"n": max(0, int(c["n"])), "len_p75": None if length is None else round(float(length), 2)}
        except (AttributeError, KeyError, TypeError, ValueError):
            pass
    out = {"version": str(det.get("version") or "meo")[:40], "photos": rows}
    crop = _fractions([det.get("crop")], 2, 1)
    if crop:
        out["crop"] = crop[0]
    return out


def _fractions(items, width, most):
    """items as at most `most` lists of `width` numbers, each 0-1 rounded to
    4 places, or None if it is not that shape at all."""
    if not isinstance(items, list):
        return None
    out = []
    for it in items[:most]:
        try:
            vals = [round(float(v), 4) for v in it][:width]
        except (TypeError, ValueError):
            return None
        if len(vals) != width or not all(0.0 <= v <= 1.0 for v in vals):
            return None
        out.append(vals)
    return out


def yolo_band_result(group, det, settings, reading_id):
    """The reading from the detector's numbers, shaped like one prompt's
    aggregated result so the rest of /inline/readings records it the same
    way. The photos still get the unusable-frame check first (422), exactly
    as on the prompt path. Returns (results, escalated, photo_mean, photo_std)."""
    from PIL import Image
    imgs = [Image.open(io.BytesIO(raw)).convert("RGB") for raw in group]
    photo_mean, photo_std = photo_stats(imgs[0])
    for img in imgs:
        why = unusable_photo(img)
        if why:
            print(f"[capture refused] {reading_id}: {why}")
            raise Refused(422, why)
    def sums(photos):
        return (round(sum(p["meo_px"] for p in photos), 1), sum(p["product_px"] for p in photos),
                sum(p["meo_n"] for p in photos))

    meo, product, pieces = sums(det["photos"])
    extra = {}
    # The dust-free numbers when every photo has them (inline_settings.
    # dust_mask): the pooled window and the lot estimates then add those up.
    clean = [p.get("clean") for p in det["photos"]]
    if all(clean):
        c_meo, c_product, c_pieces = sums(clean)
        extra["clean"] = {"meo_px": c_meo, "product_px": c_product, "meo_n": c_pieces}
        if settings["dust_mask"] and not settings["exclude_strip"] and c_product > 0:
            extra["raw"] = {"meo_px": meo, "product_px": product, "meo_n": pieces}
            meo, product, pieces = c_meo, c_product, c_pieces
    # Without the dust strip (inline_settings.exclude_strip), when every
    # photo has those numbers; otherwise the reading stays as measured.
    strip = [p.get("strip") for p in det["photos"]]
    version = f"yolo-{det['version']}"
    if all(strip):
        s_meo, s_product, s_pieces = sums(strip)
        extra["strip"] = {"meo_px": s_meo, "product_px": s_product, "meo_n": s_pieces}
        if settings["exclude_strip"] and s_product > 0:
            extra["raw"] = {"meo_px": meo, "product_px": product, "meo_n": pieces}
            meo, product, pieces = s_meo, s_product, s_pieces
            version += "+strip"
    # Bicolore over the whole crop's product, when every photo has it: a raw
    # share (no scale), shown in SGSC per photo and per lot, never decided on.
    bi = [p.get("bicolore") for p in det["photos"]]
    total_product = sum(p["product_px"] for p in det["photos"])
    if all(bi) and total_product > 0:
        bi_px = sum(b["px"] for b in bi)
        extra["bicolore"] = {"n": sum(b["n"] for b in bi), "px": bi_px, "product_px": total_product,
                             "ratio_pct": round(100 * bi_px / total_product, 4)}
    ratio = meo / product
    estimate = round(settings["yolo_scale"] * 100 * ratio, 2)
    result = {
        "band": band_for_pct(estimate, "coarse6"),
        "estimate_pct": estimate,
        "prompt_version": version,
        "model": "yolo-onnx (Ariel)",
        "repeat_count": 1,
        "justification": f"Détecteur : {pieces} morceau(x) de MEO sur {len(group)} photo(s).",
        "measures": {"yolo": {**det, "ratio_pct": round(100 * ratio, 4), "scale": settings["yolo_scale"],
                              "meo_px": round(meo, 1), "product_px": product, "meo_n": pieces,
                              "dust_mask": "raw" in extra and not version.endswith("+strip"),
                              "strip_excluded": version.endswith("+strip"), **extra}},
    }
    return [result], False, photo_mean, photo_std


def inline_instruction(decision_pct, n, settings, last_ack_instruction=None):
    """(instruction, alert, reason) for the inline rig from its decision value.

    Too few readings since a start or a confirmation: hold. AQ is alerted on
    the threshold (alert_at), or when AUGMENTER comes back once the readings
    after a confirmed AUGMENTER are in -- the adjustment did not suffice. That
    replaces the tray's count of consecutive AUGMENTER here: with a reading a
    minute and a median over ten, any real episode is ten in a row before
    anyone could have reacted.
    """
    if decision_pct is None or n < settings["decision_min_readings"]:
        return "hold", False, None
    instruction, alert = operator_instruction(decision_pct, settings)
    if alert:
        return instruction, True, "threshold"
    # With adaptive turns a second AUGMENTER is the plan, not a failure: AQ
    # hears from inline_turn when the run of turns reaches its limit instead.
    if ((instruction or "").startswith("increase") and (last_ack_instruction or "").startswith("increase")
            and not settings["adaptive_turns"]):
        return instruction, True, "streak"
    return instruction, False, None


def _inline_response(reading_id, decider, settings, recorded_id=None):
    """What the Pi needs to draw the screen. target is always None for now:
    the screen can show "Régler à X", but nothing yet knows what X should be.
    It comes once readings and confirmed settings have been recorded side by
    side for long enough to fit one against the other."""
    return {
        "reading_id": reading_id,
        "mode": settings["mode"],
        "band": decider.get("band") or decider.get("predicted_band"),
        "estimate_pct": decider.get("estimate_pct"),
        "instruction": decider.get("instruction", decider.get("operator_instruction")),
        "alert": bool(decider.get("instruction_alert")),
        "alert_reason": decider.get("alert_reason"),
        "alert_streak": settings["alert_consecutive_increase"],
        # The median the instruction was decided on, and over how many
        # readings; absent on a retry (not recorded, only the instruction is).
        "decision_pct": decider.get("decision_pct"),
        "decision_n": decider.get("decision_n"),
        "decision_min": settings["decision_min_readings"],
        # Minutes pooled when the detector decides over a window (0: a median).
        "decision_window_min": settings["decision_window_min"] if settings["decider"] == "yolo" else 0,
        # How far to turn the handle -- whole cm (turn_cm) or whole quarter
        # turns (turn_qt), within TURN_UNITS; None: the screen's own fixed
        # text in turn_unit -- what it was sized on, and the seconds still
        # left before the photos after a turn count.
        "turn_unit": decider.get("turn_unit") or settings["turn_unit"],
        "turn_cm": safe_turn(decider["turn"], "cm")
                   if decider.get("turn") is not None and (decider.get("turn_unit") or "cm") == "cm" else None,
        "turn_qt": safe_turn(decider["turn"], "quarter")
                   if decider.get("turn") is not None and decider.get("turn_unit") == "quarter" else None,
        "turn_basis": decider.get("turn_basis"),
        "settle_left_s": decider.get("settle_left_s") or 0,
        # This reading on the lab scale, and the line that put it there (None
        # without a calibration: the screen then shows the band).
        "calibrated_pct": decider.get("calibrated_pct"),
        "calibration": settings.get("calibration"),
        # How the Pi should take the next reading: a burst of group_size photos.
        "group_size": settings["group_size"],
        "group_spacing_s": settings["group_spacing_s"],
        # ...extended up to group_max photos until the burst's summed product
        # coverage reaches group_target_cover (0 = fixed group_size).
        "group_max": settings["group_max"],
        "group_target_cover": settings["group_target_cover"],
        # The lines the instruction is decided against, for the screen's trend.
        "thresholds": {"decrease_below": settings["decrease_below"],
                       "increase_at": settings["increase_at"]},
        "target": None,
        "photo_mean": decider.get("photo_mean"),
        "photo_std": decider.get("photo_std"),
        "next_in_s": next_reading_s(settings, decider.get("correcting")),
        "recorded_id": recorded_id or decider.get("id"),
    }


# --- One reading: Caliban's /inline/readings, without the prompts -------------


def previous_reply(reading_id, settings):
    """The reading already recorded under this id, for a retry after a dropped
    connection, or None."""
    done = _query("""select id, predicted_band, estimate_pct, operator_instruction, instruction_alert,
                            photo_mean, photo_std, mesures
                     from public.vision_band_estimates
                     where source = 'inline' and lot_number_text = %s and operator_instruction is not null
                     limit 1""", (reading_id,))
    if not done:
        return None
    consigne = (done[0].get("mesures") or {}).get("consigne") or {}
    done[0]["turn"], done[0]["turn_basis"] = consigne.get("turn", consigne.get("turn_cm")), consigne.get("basis")
    done[0]["turn_unit"] = consigne.get("unit") or "cm"
    return _inline_response(reading_id, done[0], settings)


def decide(data, paths):
    """(reply, row): what the screen is told and what is recorded, from the same
    fields and photos the loop would post to Caliban. row is None on a retry, when
    the reading is already recorded. Raises Refused. Writes nothing."""
    reading_id = data["reading_id"]
    settings = inline_settings()
    # Also where unreadable settings end up: inline_settings falls back to "ai".
    if settings["decider"] != "yolo":
        raise Refused(503, "Réglages illisibles ou détecteur non choisi : lecture non analysée.")

    again = previous_reply(reading_id, settings)
    if again:
        return again, None

    group = []
    for p in paths:
        with open(p, "rb") as f:
            group.append(f.read())

    covers = []
    for part in (data.get("couverture") or "").split(",")[:len(group)]:
        try:
            v = float(part)
            covers.append(round(v, 3) if 0.0 <= v <= 1.0 else None)
        except ValueError:
            covers.append(None)
    if not any(c is not None for c in covers):
        covers = []

    det = inline_detector(data.get("detecteur") or "", len(group))
    if det is None:
        raise Refused(503, "Détecteur MEO absent sur Ariel : lecture non analysée.")
    product_px = sum(p["product_px"] for p in det["photos"])
    if product_px < YOLO_MIN_PRODUCT_PX * len(det["photos"]):
        raise Refused(409, "Pas assez de produit dans la photo : lecture non analysée.")
    results, escalated, photo_mean, photo_std = yolo_band_result(group, det, settings, reading_id)

    since_ack_s = data.get("since_ack_s") or ""
    last_ack_instruction = (data.get("last_ack_instruction") or "").strip() or None
    try:
        since_ack = float(since_ack_s) if since_ack_s.strip() else None
    except ValueError:
        since_ack = None
    decider = results[0]
    # The detector's estimate is on the lab scale already: no calibration line.
    dec_settings = {**settings, "calibration": None}
    ctx = inline_ack_context(since_ack, last_ack_instruction, data)
    # A turn of the handle -- an older loop.py does not say which choice,
    # and a turn is what nearly every confirmation is.
    turned = (ctx["last_choice"] in (None, "adjusted", "set")
              and bool(ctx["last_instruction"]))
    if settings["decision_window_min"]:
        decision_pct, decision_n, pooled = inline_window_decision(
            decider["measures"]["yolo"], decider.get("prompt_version"), settings, since_ack, turned)
        if pooled != decision_pct:
            # One photo would have decided otherwise (decision_drop_one):
            # what the window said with it, for replaying the rule later.
            decider["measures"]["fenetre"] = {"pooled_pct": pooled, "decision_pct": decision_pct}
    else:
        decision_pct, decision_n = inline_decision(
            decider.get("estimate_pct"), decider.get("prompt_version"), dec_settings, since_ack)
    decision_pct = inline_calibrated(decision_pct, dec_settings)
    decider["calibrated_pct"] = inline_calibrated(decider.get("estimate_pct"), dec_settings)
    decider["decision_pct"], decider["decision_n"] = decision_pct, decision_n
    decider["correcting"] = turned and decision_n < settings["decision_min_readings"]
    instruction, alert, reason = inline_instruction(decision_pct, decision_n, settings, last_ack_instruction)
    instruction, turn, turn_basis, limit_reason = inline_turn(instruction, decision_pct, settings, ctx)
    if limit_reason:
        alert, reason = True, limit_reason
    decider["turn_unit"], decider["turn_basis"] = settings["turn_unit"], turn_basis
    decider["turn"] = turn
    decider["settle_left_s"] = settling_s(since_ack, settings, turned)
    decider["instruction"], decider["instruction_alert"], decider["alert_reason"] = instruction, alert, reason
    decider["photo_mean"], decider["photo_std"] = photo_mean, photo_std

    storage_path = f"inline/{reading_id}.jpg"
    group_paths = [storage_path] + [f"inline/{reading_id}-{k}.jpg" for k in range(2, len(group) + 1)]
    mesures = dict(decider.get("measures") or {})
    if len(group_paths) > 1:
        mesures["groupe"] = group_paths
    if covers:
        mesures["couverture"] = covers
        known = [c for c in covers if c is not None]
        if known:
            mesures["couverture_totale"] = round(sum(known), 3)
    if decider.get("turn_basis") or decider.get("decision_n") is not None:
        # How far the screen said to turn, and on what: the next turn is
        # sized from it, and turns can be checked against what followed.
        # On a hold too: decision_n is what tells SGSC "too few photos yet"
        # from a real "no change", which it cannot work out once a wait
        # after a turn (settle_min) leaves photos out.
        mesures["consigne"] = {"decision_pct": decider.get("decision_pct"),
                               "decision_n": decider.get("decision_n"),
                               "turn": decider.get("turn"), "unit": decider.get("turn_unit"),
                               "basis": decider["turn_basis"]}
    # Only what the detector path sets; the prompts' columns stay at their defaults.
    row = {
        "lot_number_text": reading_id,
        "source": "inline",
        "predicted_band": decider.get("band"),
        "justification": decider.get("justification"),
        "storage_path": storage_path,
        "model": decider.get("model"),
        "is_training": False,
        "prompt_version": decider.get("prompt_version"),
        "repeat_count": decider.get("repeat_count", 1),
        "estimate_pct": decider.get("estimate_pct"),
        "operator_instruction": instruction,
        "instruction_alert": alert,
        "escalated": escalated,
        "photo_mean": photo_mean,
        "photo_std": photo_std,
        "mesures": mesures,
    }
    return _inline_response(reading_id, decider, settings), row


# --- The switch: the Pi records what it decided (ARIEL_DECIDES=1) ---------------


def unreachable(e):
    """True when the database could not be reached or answered too slowly, as
    opposed to a fault in this code: loop.py shows the first as no connection."""
    try:
        import psycopg2
    except ImportError:
        return False
    return isinstance(e, (psycopg2.OperationalError, psycopg2.InterfaceError))


def upload_photos(reading_id, paths, api_key, log):
    """Each photo's review copy to rig-upload, where Caliban stored them:
    inline/<id>.jpg, then inline/<id>-2.jpg and so on. Returns the stored path
    of each, None where it failed; a failed photo costs its copy, never the
    reading, as in Caliban."""
    import requests
    stored = []
    for k, p in enumerate(paths, start=1):
        params = {"reading_id": reading_id, **({"k": str(k)} if k > 1 else {})}
        try:
            with open(p, "rb") as f:
                body = review_copy(f.read())
            resp = requests.post(UPLOAD_URL, params=params, data=body, timeout=UPLOAD_TIMEOUT,
                                 headers={"X-API-Key": api_key, "Content-Type": "image/jpeg"})
            resp.raise_for_status()
            stored.append(resp.json()["path"])
        except Exception as e:
            log(f"[inline photo upload failed] {reading_id}-{k}: {type(e).__name__}: {e}")
            stored.append(None)
    return stored


def record(data, paths, api_key, log):
    """What Caliban's /inline/readings did, here: decide, store the photos, record
    the reading. Returns the reply. Raises Refused like decide(), and a database
    error when it can't be reached (see unreachable)."""
    # First, because the settings lookup swallows a lost connection and falls
    # back to defaults, which decide() refuses as unreadable settings: right for
    # the shadow, but here the screen must say "no connection", as it did when
    # Caliban was out of reach.
    _query("select 1 as up")
    reply, row = decide(data, paths)
    if row is None:
        # Already recorded: a retry of a reading whose answer was lost.
        return reply
    stored = upload_photos(data["reading_id"], paths, api_key, log)
    row["storage_path"] = stored[0]
    # As Caliban: only the photos that were stored, and only for a burst.
    group = [p for p in stored if p]
    if len(group) > 1:
        row["mesures"]["groupe"] = group
    else:
        row["mesures"].pop("groupe", None)
    try:
        reply["recorded_id"] = str(_query("select public.ariel_record_reading(%s::jsonb) as id",
                                          (json.dumps(row, default=str),))[0]["id"])
    except Exception as e:
        # Caliban showed the instruction even when recording failed; so does this.
        log(f"[inline recording failed] {data['reading_id']}: {type(e).__name__}: {e}")
    return reply


def record_acks(batch):
    """Operator confirmations; a repeat of one already recorded is ignored."""
    return _query("select public.ariel_record_acks(%s::jsonb) as n", (json.dumps(batch),))[0]["n"]


def set_status(payload):
    """The screen's state, for SGSC's mirror (destoner-mirror.html)."""
    _query("select public.ariel_set_status(%s::jsonb) as done", (json.dumps(payload, default=str),))


# --- Shadow: beside Caliban, compared on every reading -------------------------

# recorded_id is the row Caliban just made; there is nothing to compare it with.
NOT_COMPARED = {"recorded_id"}
DIFFS_KEPT = 10


def differences(pi, caliban):
    """{field: [pi, caliban]} over both dicts' keys, JSON-shaped first so a tuple
    and a list, or 1 and 1.0, are not a difference."""
    a, b = json.loads(json.dumps(pi)), json.loads(json.dumps(caliban, default=str))
    return {k: [a.get(k), b.get(k)] for k in sorted(set(a) | set(b))
            if k not in NOT_COMPARED and a.get(k) != b.get(k)}


def shadow_before(data, paths, log):
    """Work the reading out here, before Caliban records it, so the window both
    pool from is the same. Returns what shadow_after compares; never raises."""
    started = time.monotonic()
    out = {"reply": None, "row": None, "refused": None, "failed": None}
    try:
        out["reply"], out["row"] = decide(data, paths)
    except Refused as e:
        out["refused"] = f"{e.status}: {e.detail}"
    except Exception as e:
        out["failed"] = f"{type(e).__name__}: {e}"
        log(f"shadow: {out['failed']}")
    out["s"] = round(time.monotonic() - started, 2)
    return out


def shadow_after(shadow, reading_id, reply, error, state, log):
    """Compare with what Caliban answered and recorded, and tally it in state.
    Never raises: a fault here must not cost a reading."""
    try:
        t = state.setdefault("decide_shadow", {"since": _now_iso(), "same": 0, "different": 0, "failed": 0,
                                               "row_same": 0, "row_different": 0, "diffs": []})
        t["last_s"] = shadow["s"]
        if shadow["failed"]:
            t["failed"] += 1
            t["last_failure"] = shadow["failed"][:200]
            return
        if error or shadow["refused"]:
            diffs = {} if bool(error) == bool(shadow["refused"]) and error[:3] == shadow["refused"][:3] \
                else {"refused": [shadow["refused"], error]}
        else:
            diffs = differences(shadow["reply"], reply)
        if shadow["row"] and not error:
            stored = _query("""select * from public.vision_band_estimates
                               where source = 'inline' and lot_number_text = %s
                                 and operator_instruction is not null limit 1""", (reading_id,))
            row_diffs = differences(shadow["row"], {k: stored[0].get(k) for k in shadow["row"]}) if stored \
                else {"row": ["expected", None]}
            t["row_different" if row_diffs else "row_same"] += 1
            diffs.update({f"row.{k}": v for k, v in row_diffs.items()})
        if diffs:
            t["different"] += 1
            t["diffs"] = (t["diffs"] + [{"id": reading_id, **diffs}])[-DIFFS_KEPT:]
            log(f"shadow {reading_id} differs: {json.dumps(diffs, default=str)[:600]}")
        else:
            t["same"] += 1
    except Exception as e:
        log(f"shadow comparison failed: {type(e).__name__}: {e}")


def _now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
