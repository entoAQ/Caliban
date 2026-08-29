"""
Contamination screening inference service.

Photos now live in SharePoint, not Supabase Storage -- Supabase holds
only lightweight metadata (which lot, which detections, which labels),
never the image bytes themselves. See sharepoint_client.py for the
Graph API integration this relies on.

Run locally:
  export SUPABASE_URL=https://xxxxx.supabase.co
  export SUPABASE_SERVICE_KEY=your-service-role-key
  export MS_TENANT_ID=... MS_CLIENT_ID=... MS_CLIENT_SECRET=...
  export SHAREPOINT_HOSTNAME=... SHAREPOINT_SITE_PATH=...
  export POWER_AUTOMATE_API_KEY=... (any long random string you choose)
  uvicorn app.main:app --reload --port 8000
"""
import asyncio
import datetime
import hashlib
import io
import json
import os
import re
import statistics
import time
import uuid

# Set once, the moment this process actually starts (module import time --
# exactly when a container boots). Exposed via /health specifically to
# answer "has my latest change actually taken effect" directly, rather
# than guessing based on how long ago a deploy command was run -- compare
# this timestamp against when you committed/pushed a change.
STARTUP_TIME = datetime.datetime.now(datetime.timezone.utc).isoformat()

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Form, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response

from app.supabase_client import supabase, get_current_operator
from app import sharepoint_client

MODEL_VERSION = os.environ.get("MODEL_VERSION", "contamination_v1")
MODEL_PATH = os.environ.get("MODEL_PATH", "models/best.pt")
TRAINING_FOLDER = os.environ.get("SHAREPOINT_TRAINING_FOLDER", "TrainingPhotos")
SCREENING_FOLDER = os.environ.get("SHAREPOINT_SCREENING_FOLDER", "ScreeningPhotos")
POWER_AUTOMATE_API_KEY = os.environ.get("POWER_AUTOMATE_API_KEY")

# Deliberately a separate secret from POWER_AUTOMATE_API_KEY rather than
# reusing it. The rig is physically accessible hardware sitting on a plant
# floor; Power Automate is a cloud tenant. Sharing one key would mean that
# rotating it after someone walks off with a Raspberry Pi also breaks the
# Power Automate integration, which is exactly the coupling you do not want
# during an incident.
RIG_API_KEY = os.environ.get("RIG_API_KEY")

app = FastAPI(title="Contamination Screening Inference API")

# Restrict to your actual frontend origin in production
FRONTEND_ORIGIN = os.environ.get("FRONTEND_ORIGIN", "*")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_methods=["*"],
    allow_headers=["*"],
)

_model = None
_model_load_failed = False


def get_model():
    """Returns the loaded model, or None if no trained model exists yet --
    never raises for the "not trained yet" case specifically, since a
    missing model shouldn't block photo collection, only detection itself."""
    global _model, _model_load_failed
    if _model is not None or _model_load_failed:
        return _model
    if not os.path.exists(MODEL_PATH):
        _model_load_failed = True
        return None
    from ultralytics import YOLO
    _model = YOLO(MODEL_PATH)
    return _model


def verify_power_automate_key(x_api_key: str = Header(None)):
    """Separate auth path from the browser-facing endpoints below --
    Power Automate isn't a logged-in SGSC user, so it can't present a
    Supabase session token. A shared secret, checked via a header, is
    the simplest correct mechanism for a single trusted automated caller."""
    if not POWER_AUTOMATE_API_KEY or x_api_key != POWER_AUTOMATE_API_KEY:
        raise HTTPException(status_code=401, detail="Clé API invalide ou manquante.")
    return True


def verify_rig_key(x_api_key: str = Header(None)):
    """Auth for the bench rig, which is a device rather than an operator.

    Same shared-secret shape as verify_power_automate_key and for the same
    reason -- the Pi has no Supabase session to present and never will. Note
    what this deliberately does not grant: the rig can claim and complete
    capture commands, and nothing else. It cannot run an analysis, read a
    lot, or reach any of the operator-facing endpoints. A camera should be
    able to act as a camera."""
    if not RIG_API_KEY or x_api_key != RIG_API_KEY:
        raise HTTPException(status_code=401, detail="Clé API invalide ou manquante.")
    return True


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "model_loaded": get_model() is not None,
        "started_at": STARTUP_TIME,
        "prompt_version": CALIBAN_PROMPT_VERSION,
        "prompt_hash": PROMPT_HASH,
        "available_prompt_variants": list(BAND_PROMPT_VARIANTS.keys()),
        # Presence of these two confirms the capture-storage/structured-
        # factors/outlier-flagging deploy actually took effect, same way
        # available_prompt_variants confirms a prompt deploy -- check
        # this against local main.py rather than guessing from how long
        # ago you ran the deploy command.
        "band_test_capture_bucket": BAND_TEST_CAPTURE_BUCKET,
        "outlier_threshold_pts": OUTLIER_THRESHOLD_PTS,
        # Confirms the per-variant reference-photo change deployed. Should
        # list only 1.x -- 2.x runs prompt-only by design, and seeing a 2.x
        # label here means something reintroduced references silently.
        "variants_using_references": sorted(VARIANTS_USING_REFERENCES),
        # Present in BAND_PROMPT_VARIANTS and still runnable when named
        # explicitly, but hidden from the variant picker.
        "archived_prompt_variants": sorted(ARCHIVED_PROMPT_VARIANTS),
    }


# ── Browser-facing: which prompt variants exist right now ────────────
# Lets the validation UI build its variant-selection checkboxes without
# hardcoding the list -- adding a key to BAND_PROMPT_VARIANTS is enough
# to make a new candidate selectable, no frontend deploy needed.
@app.get("/prompt-variants")
async def prompt_variants(operator: dict = Depends(get_current_operator)):
    # Archived variants are omitted here but still accepted by
    # /azure-band-test, so re-scoring an old image against the prompt that
    # produced it stays possible without putting retired candidates in front
    # of an operator choosing what to run today.
    return {
        "variants": [v for v in BAND_PROMPT_VARIANTS if v not in ARCHIVED_PROMPT_VARIANTS],
        "default": DEFAULT_PROMPT_VARIANT,
    }


# ── Browser-facing: capture a photo ──────────────────────────────────
# Writes to SharePoint only -- no Supabase row at all for training-mode
# photos, by design. Screening-mode photos ALSO get no Supabase row
# here -- that row gets created later, by Power Automate, once detection
# actually completes (see /detect below). This endpoint's only job is
# getting the bytes into the right SharePoint folder under the right name.
@app.post("/vision-upload")
async def vision_upload(
    file: UploadFile = File(...),
    autoid: str = Form(...),
    correlation_id: str = Form(...),
    training_mode: bool = Form(False),
    operator: dict = Depends(get_current_operator),
):
    contents = await file.read()
    ext = os.path.splitext(file.filename)[1] or ".jpg"
    # AutoID first (so anyone browsing the SharePoint folder directly can
    # tell which lot a photo belongs to at a glance), correlation id
    # second (so an operator taking several photos of the same lot in a
    # row doesn't collide -- each upload still gets its own unique file).
    filename = f"{autoid}__{correlation_id}{ext}"
    folder = TRAINING_FOLDER if training_mode else SCREENING_FOLDER
    sharepoint_path = sharepoint_client.upload_file(folder, filename, contents, file.content_type or "image/jpeg")
    return {
        "status": "uploaded",
        "training_mode": training_mode,
        "sharepoint_path": sharepoint_path,
        "correlation_id": correlation_id,
    }


# ── Power-Automate-facing: run detection ─────────────────────────────
# Deliberately minimal: image bytes in, JSON out, nothing else. Power
# Automate's own SharePoint connector fetches the image and POSTs it
# here directly; this endpoint never touches SharePoint or Supabase --
# Power Automate is responsible for writing the result to Supabase
# itself afterward, using this JSON response plus the correlation_id it
# already extracted from the triggering file's name.
@app.post("/detect")
async def detect(
    file: UploadFile = File(...),
    _: bool = Depends(verify_power_automate_key),
):
    model = get_model()
    if model is None:
        return {"detection_status": "no_model", "model_version": None, "detections": [], "counts_by_class": {}}

    contents = await file.read()
    ext = os.path.splitext(file.filename)[1] or ".jpg"
    tmp_path = f"/tmp/{uuid.uuid4().hex}{ext}"
    with open(tmp_path, "wb") as f:
        f.write(contents)

    start = time.time()
    results = model.predict(tmp_path, verbose=False)[0]
    elapsed_ms = int((time.time() - start) * 1000)
    os.remove(tmp_path)

    names = results.names
    detections_out = []
    for box in results.boxes:
        cls_id = int(box.cls[0])
        conf = float(box.conf[0])
        x1, y1, x2, y2 = box.xyxyn[0].tolist()
        detections_out.append({
            "class_name": names[cls_id],
            "confidence": round(conf, 3),
            "bbox": [round(x1, 4), round(y1, 4), round(x2, 4), round(y2, 4)],
        })

    return {
        "detection_status": "ok",
        "model_version": MODEL_VERSION,
        "inference_time_ms": elapsed_ms,
        "detections": detections_out,
        "counts_by_class": {
            name: sum(1 for d in detections_out if d["class_name"] == name)
            for name in set(d["class_name"] for d in detections_out)
        } if detections_out else {},
    }


# ── Browser-facing: labeling tool's image proxy ──────────────────────
# The ONLY place raw image bytes ever pass through besides SharePoint
# itself -- fetched here, streamed straight to the browser, never written
# to disk or any database. Takes a SharePoint path directly (not an
# inspection_id) specifically because training photos, the ones actually
# being labeled, never have a vision_inspections row to look one up from.
@app.get("/sharepoint-image-proxy")
async def sharepoint_image_proxy(path: str, operator: dict = Depends(get_current_operator)):
    try:
        image_bytes = sharepoint_client.download_file(path)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Image introuvable sur SharePoint : {e}")
    return Response(content=image_bytes, media_type="image/jpeg")


# ── Browser-facing: list training photos available to label ─────────
@app.get("/training-photos")
async def list_training_photos(operator: dict = Depends(get_current_operator)):
    files = sharepoint_client.list_folder(TRAINING_FOLDER)
    out = []
    for f in files:
        # filenames are {autoid}__{correlation_id}.ext -- extract the
        # autoid back out for display, same convention used at upload time.
        name = f["name"]
        autoid = name.split("__")[0] if "__" in name else name
        out.append({"path": f["path"], "name": name, "autoid": autoid})
    return out


@app.get("/inspections/{lot_number}")
def get_by_lot(lot_number: str, operator: dict = Depends(get_current_operator)):
    resp = supabase.table("vision_inspections").select("*").eq("lot_number", lot_number).execute()
    return resp.data


# ── Claude Vision test endpoint ──────────────────────────────────────
# Evaluation tool, not a production decision-maker: lets a real photo be
# sent directly to Claude's vision API to see how it actually performs on
# this specific, novel domain (dried BSF larvae / frass / foreign
# material) before committing effort to a custom-trained detector.
# Deliberately does NOT write to vision_inspections -- that table's
# schema (model_version, detection_status) is shaped around the YOLO
# bounding-box flow; forcing this exploratory tool into it would be
# premature. No persistence at all for now -- add it later if this
# approach actually proves out.
#
# Requires ANTHROPIC_API_KEY as a Render environment variable -- NEVER
# passed to or stored in the browser, same reasoning as the Supabase
# service_role key already handled this way.

import base64
import anthropic

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

CLAUDE_VISION_PROMPT = """Tu examines une photo prise dans une installation de production \
de larves de mouche soldat noire (Hermetia illucens), pour le dépistage de contamination. \
Le contenu attendu est un mélange de larves séchées et de frass (résidu d'élevage). \
On cherche spécifiquement toute trace de matière étrangère : plastique, métal ou verre.

Réponds selon cette structure exacte :

COMPOSITION GÉNÉRALE : décris ce que tu observes (proportion approximative larves/frass, \
apparence générale).

MATIÈRE ÉTRANGÈRE DÉTECTÉE : Oui ou Non. Si oui, décris précisément quoi et où dans l'image \
(ex. « coin supérieur droit », « au centre parmi les larves »).

NIVEAU DE CONFIANCE : indique honnêtement ton niveau de certitude. Ce type d'image est un \
domaine visuel que tu n'as probablement pas beaucoup rencontré en entraînement -- signale \
clairement si quelque chose te semble ambigu plutôt que de deviner.

Ne tente pas de compter précisément les particules individuelles si elles sont nombreuses et \
serrées -- donne plutôt une impression qualitative (peu, modéré, abondant)."""

# Deliberately different from CLAUDE_VISION_PROMPT above -- that one asks
# for open-ended description; this one forces a single, specific band
# choice with a strict output format, specifically so it can be parsed
# reliably and logged automatically for comparison against real,
# lab-confirmed ME% values. Explicitly does NOT ask for a count -- asks
# for the same kind of holistic density impression a person forms
# glancing at a tray, since that's the more plausible task for a general
# vision model, not counting individual touching objects.
#
# A dict of label -> prompt text, not a single string -- lets several
# candidate prompts be tested side by side against the exact same photo
# in one /azure-band-test call (see `variants` param below), instead of
# manually re-running the same capture once per candidate. Adding a new
# candidate means adding a new key here; existing keys are never
# rewritten in place, so old variants stay runnable for as long as
# they're still useful for comparison. Retire a key by deleting it once
# it's no longer worth calling -- its past results stay in
# vision_band_estimates either way, keyed by the prompt_hash they were
# actually produced with.
BAND_PROMPT_VARIANTS = {
    "1.3": """Tu examines une photo d'un échantillon de larves de mouche soldat noire \
(Hermetia illucens) mélangées à de la MEO (matières étrangères organiques, résidu d'élevage \
aussi appelé frass), pour estimer le niveau de contamination visible.

Important : mesure la densité par rapport à la surface couverte par l'échantillon lui-même \
(larves + MEO), PAS par rapport à l'ensemble de la photo. Le plateau contient souvent de \
l'espace vide autour de l'échantillon pour permettre un étalement en une seule couche -- cet \
espace vide ne doit jamais être compté comme faisant partie d'un échantillon "propre".

Attention à ne pas confondre une prépupe avec de la MEO/frass. En approchant le stade de \
prépupe, la larve fonce considérablement -- brun foncé à presque noir -- et peut, par sa \
seule couleur, ressembler à un amas de frass. Une prépupe reste une larve normale du produit, \
PAS une matière étrangère : avant de compter un élément foncé comme de la MEO, regarde sa \
forme (silhouette allongée et segmentée d'une larve, souvent encore reconnaissable même très \
foncée) plutôt que sa seule teinte.

Ignore aussi les fragments minuscules -- poussière fine, résidu pulvérulent, grains isolés de \
la taille d'un point -- qui ne forment pas un amas ou une particule clairement visible \
individuellement. Seule une matière qui se distingue nettement à l'œil nu, comme le ferait un \
inspecteur qui jette un coup d'œil rapide au plateau (pas un examen à la loupe), doit compter \
dans l'estimation de densité.

Ne tente PAS de compter les particules individuelles -- donne une impression visuelle globale \
de densité, comme le ferait une personne qui regarde rapidement le plateau.

Réponds EXACTEMENT selon ce format, rien d'autre avant ou après :

BANDE: [une seule valeur parmi : <1%, 1-3%, 3-7%, 7-10%, 10-14%, >14%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [liste séparée par virgules, uniquement parmi : prepupes, fragments_ecrases, poussiere, \
densite_reelle, autre -- les éléments qui ont RÉELLEMENT influencé ce choix de bande sur cette \
photo précise, pas une liste générique]
JUSTIFICATION: [une phrase, ce qui a motivé ce choix]""",

    # Candidate -- NOT the default yet (see DEFAULT_PROMPT_VARIANT below).
    # Identical to "1.3" except for one added paragraph anchoring the
    # low end of the scale, targeting a specific pattern in a week of
    # real v1.3 traffic: real values under ~2% (1.42%, 1.62%, 1.74%,
    # 1.79%, 1.91%) repeatedly got read as "3-7%" instead of "<1%"/"1-3%"
    # -- a jump straight past the correct band rather than a
    # boundary-adjacent miss. Deliberately scoped to that specific
    # <1%/1-3% vs 3-7% boundary, not a blanket "lean lower" nudge (the
    # same one-directional mistake v1.2 already corrected for once).
    "1.4": """Tu examines une photo d'un échantillon de larves de mouche soldat noire \
(Hermetia illucens) mélangées à de la MEO (matières étrangères organiques, résidu d'élevage \
aussi appelé frass), pour estimer le niveau de contamination visible.

Important : mesure la densité par rapport à la surface couverte par l'échantillon lui-même \
(larves + MEO), PAS par rapport à l'ensemble de la photo. Le plateau contient souvent de \
l'espace vide autour de l'échantillon pour permettre un étalement en une seule couche -- cet \
espace vide ne doit jamais être compté comme faisant partie d'un échantillon "propre".

Attention à la zone basse de l'échelle en particulier : à peine quelques petites taches ou \
grains isolés et bien espacés sur l'échantillon correspond typiquement à <1% ou 1-3%, PAS à \
3-7%. Réserve 3-7% aux cas où la matière étrangère forme un ensemble de taches ou d'amas \
visibles sur une bonne partie de la surface de l'échantillon -- pas seulement quelques points \
épars ici et là.

Attention à ne pas confondre une prépupe avec de la MEO/frass. En approchant le stade de \
prépupe, la larve fonce considérablement -- brun foncé à presque noir -- et peut, par sa \
seule couleur, ressembler à un amas de frass. Une prépupe reste une larve normale du produit, \
PAS une matière étrangère : avant de compter un élément foncé comme de la MEO, regarde sa \
forme (silhouette allongée et segmentée d'une larve, souvent encore reconnaissable même très \
foncée) plutôt que sa seule teinte.

Ignore aussi les fragments minuscules -- poussière fine, résidu pulvérulent, grains isolés de \
la taille d'un point -- qui ne forment pas un amas ou une particule clairement visible \
individuellement. Seule une matière qui se distingue nettement à l'œil nu, comme le ferait un \
inspecteur qui jette un coup d'œil rapide au plateau (pas un examen à la loupe), doit compter \
dans l'estimation de densité.

Ne tente PAS de compter les particules individuelles -- donne une impression visuelle globale \
de densité, comme le ferait une personne qui regarde rapidement le plateau.

Réponds EXACTEMENT selon ce format, rien d'autre avant ou après :

BANDE: [une seule valeur parmi : <1%, 1-3%, 3-7%, 7-10%, 10-14%, >14%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [liste séparée par virgules, uniquement parmi : prepupes, fragments_ecrases, poussiere, \
densite_reelle, autre -- les éléments qui ont RÉELLEMENT influencé ce choix de bande sur cette \
photo précise, pas une liste générique]
JUSTIFICATION: [une phrase, ce qui a motivé ce choix]""",

    # Candidate -- NOT the default yet. Built on "1.4" (not "1.3"), adding
    # one more paragraph, so running "1.3"/"1.4"/"1.5" together isolates
    # two separate questions: did the low-end anchor (1.4) help, and does
    # the crushed-larvae color gate (this) help further on top of it.
    #
    # Targets a failure mode flagged directly by the operator, not mined
    # from vision_band_estimates: crushed/broken larvae fragments --
    # routine handling damage, not contamination -- were suspected as the
    # single biggest driver of over-reads. BSF larvae flesh is pale/cream
    # when exposed by breakage, distinctly different from frass/MEO's
    # brown-to-black color, so this is phrased as a color gate rather than
    # a blanket "ignore small pieces" rule: a pale fragment reads as
    # broken larva (ignored), a dark one still counts. That also
    # subsumes the "just ignore small fragments" framing the operator
    # raised as an alternative -- most crushed-larvae debris is pale, so
    # the color gate ignores it too, but without also blinding the model
    # to small dark MEO fragments the way a pure size rule would.
    "1.5": """Tu examines une photo d'un échantillon de larves de mouche soldat noire \
(Hermetia illucens) mélangées à de la MEO (matières étrangères organiques, résidu d'élevage \
aussi appelé frass), pour estimer le niveau de contamination visible.

Important : mesure la densité par rapport à la surface couverte par l'échantillon lui-même \
(larves + MEO), PAS par rapport à l'ensemble de la photo. Le plateau contient souvent de \
l'espace vide autour de l'échantillon pour permettre un étalement en une seule couche -- cet \
espace vide ne doit jamais être compté comme faisant partie d'un échantillon "propre".

Attention à la zone basse de l'échelle en particulier : à peine quelques petites taches ou \
grains isolés et bien espacés sur l'échantillon correspond typiquement à <1% ou 1-3%, PAS à \
3-7%. Réserve 3-7% aux cas où la matière étrangère forme un ensemble de taches ou d'amas \
visibles sur une bonne partie de la surface de l'échantillon -- pas seulement quelques points \
épars ici et là.

Attention à ne pas confondre une prépupe avec de la MEO/frass. En approchant le stade de \
prépupe, la larve fonce considérablement -- brun foncé à presque noir -- et peut, par sa \
seule couleur, ressembler à un amas de frass. Une prépupe reste une larve normale du produit, \
PAS une matière étrangère : avant de compter un élément foncé comme de la MEO, regarde sa \
forme (silhouette allongée et segmentée d'une larve, souvent encore reconnaissable même très \
foncée) plutôt que sa seule teinte.

Attention aussi aux fragments de larves brisées ou écrasées -- fréquents lors de la \
manipulation, et normaux, PAS de la contamination. Une larve cassée expose une chair pâle, \
blanchâtre ou crème, nettement différente de la couleur brun-à-noir du frass et de la MEO. \
Avant de compter un petit fragment comme de la MEO, vérifie sa couleur : un fragment pâle ou \
de la même teinte que les larves intactes est probablement un morceau de larve brisée, pas de \
la matière étrangère. Ne compte un petit fragment comme de la MEO que s'il est clairement plus \
foncé (brun à noir) que la chair d'une larve.

Ignore aussi les fragments minuscules -- poussière fine, résidu pulvérulent, grains isolés de \
la taille d'un point -- qui ne forment pas un amas ou une particule clairement visible \
individuellement. Seule une matière qui se distingue nettement à l'œil nu, comme le ferait un \
inspecteur qui jette un coup d'œil rapide au plateau (pas un examen à la loupe), doit compter \
dans l'estimation de densité.

Ne tente PAS de compter les particules individuelles -- donne une impression visuelle globale \
de densité, comme le ferait une personne qui regarde rapidement le plateau.

Réponds EXACTEMENT selon ce format, rien d'autre avant ou après :

BANDE: [une seule valeur parmi : <1%, 1-3%, 3-7%, 7-10%, 10-14%, >14%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [liste séparée par virgules, uniquement parmi : prepupes, fragments_ecrases, poussiere, \
densite_reelle, autre -- les éléments qui ont RÉELLEMENT influencé ce choix de bande sur cette \
photo précise, pas une liste générique]
JUSTIFICATION: [une phrase, ce qui a motivé ce choix]""",

    # Written in English, against the calibrated rig, and the first variant
    # that can state physical facts rather than describe an appearance.
    #
    # Everything in the image is now measured. The frame is 402mm across at
    # 11.54 px/mm, illumination is flat to 1.5% after the flat-field
    # correction, white balance is locked against the tray itself, and focus
    # resolves about 0.35mm. That turns vague instructions into concrete
    # ones: "ignore fine dust" becomes a stated size in millimetres, and a
    # larva becomes a ruler the model can see in the same photo.
    #
    # English because the earlier French prompts were written for a French
    # UI, not because the model reads French worse -- but every one of these
    # has been edited by hand a dozen times, and the accumulated phrasing is
    # harder to reason about in a second language than it is worth. The
    # response keys stay French: parse_band_response looks for BANDE etc, and
    # changing both at once would confound a prompt comparison with a parser
    # change.
    #
    # No reference photos: not opted into VARIANTS_USING_REFERENCES. The
    # existing set was shot on the old camera, old dish and old lighting, and
    # grounding a calibrated tray photo against them is worse than sending
    # none -- which the bench already showed once, when turning references
    # off moved a known-0% sample from "3-7%" to "<1%, high confidence".
    "3.0": """You are looking at a photograph of black soldier fly larvae (Hermetia illucens) scattered on a white tray, taken by a fixed calibration rig. Estimate how much MEO is visible -- organic foreign matter, the rearing residue also called frass.

The photograph is taken under controlled conditions you can rely on. The camera is directly overhead and square to the tray, the lighting is even across the whole frame, and the colours are fixed. The frame is 400 mm wide and the whole of it is tray. A larva is 15 to 20 mm long, so it spans roughly one twenty-fifth of the image width -- use that as your ruler.

Judge density against the area the sample itself covers -- larvae plus MEO -- and NOT against the whole photograph. The larvae are spread thinly so that they lie in a single layer, so a large part of the frame is bare white tray. Bare tray is empty space. It is neither contamination nor evidence of a clean sample, and it must not enter the estimate either way.

Count material you could see at a glance. Using the larva as a ruler, that means particles down to about 1 mm -- roughly one fifteenth of a larva's length. Anything finer than that is dust and powder residue: ignore it. Judge as an inspector glancing at the tray would, not as someone with a magnifying glass.

Two things look like MEO and are not.

A prepupa is a normal larva, not foreign matter. Approaching pupation a larva darkens considerably, to dark brown or nearly black, and by colour alone can resemble a clump of frass. Before counting anything dark as MEO, look at its shape: a larva keeps its elongated, clearly segmented outline even when very dark.

A crushed or broken larva is also product, not contamination. Larval flesh is pale and cream-coloured where breakage exposes it, plainly different from the brown-to-black of frass. Judge these by colour: a pale fragment is a broken larva and does not count, a dark one still does.

Be careful at the low end of the scale. A few small isolated specks, well separated across the sample, is typically <1% or 1-3% -- not 3-7%. Reserve 3-7% for when foreign matter forms visible specks or clumps across a good part of the sample area, while the larvae still plainly dominate. Above that, at 7-10%, dark material is a continuous presence rather than scattered incidents: there is some in every part of the sample, and it begins to read as a component of the mixture rather than as debris within it.

Do NOT try to count individual particles. Give an overall visual impression of density, as a person glancing at the tray would.

Answer EXACTLY in this format, with nothing before or after:

BANDE: [a single value among : <1%, 1-3%, 3-7%, 7-10%, 10-14%, >14%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [comma-separated list, only from : prepupes, fragments_ecrases, poussiere, densite_reelle, autre -- the factors that ACTUALLY influenced this band choice on this specific photo, not a generic list]
JUSTIFICATION: [one sentence, in French, what drove this choice]""",

    # First variant written for a NEW PHYSICAL METHOD rather than as a
    # wording refinement: the sample is now presented in a filled, levelled
    # circular dish (~16 cm) instead of scattered in a single layer on an
    # open tray. That makes the denominator a constant -- the dish interior
    # -- instead of something the model had to infer for itself, so 1.3's
    # empty-space paragraph is not merely reworded here: it described an
    # arrangement that no longer exists, and a false premise is worse than
    # no premise. Also adds an explicit instruction to ignore everything
    # outside the dish (bench, rim, shadow, calibration tile), since the new
    # framing deliberately puts a colour/scale reference in the frame.
    #
    # Carries forward 1.5's crushed-larvae colour gate: it came from the
    # operator handling the physical product, not from mining
    # vision_band_estimates, and nothing about the presentation change
    # affects whether it's true. Deliberately does NOT carry 1.4's low-end
    # anchor -- that one was fitted to a specific pattern in old-rig data
    # (real values under ~2% reading as 3-7%) and may not survive the method
    # change; "2.1" exists to test exactly that rather than assume it.
    #
    # Note what is deliberately ABSENT: any suggestion that material may be
    # hidden below the visible surface. That is literally true of a levelled
    # dish, and saying it is precisely the one-directional upward nudge that
    # v1.1 carried and v1.2 was written to remove. The surface-vs-bulk bias
    # is systematic and gets absorbed by calibration against lab ME%;
    # telling the model to guess upward would stack variance on top of it.
    #
    # Size language ("de la taille d'un point", "à l'œil nu") is left as-is
    # on purpose: the camera has NOT changed yet, so apparent scale is
    # unchanged. When the rig moves to the Pi camera, these become wrong --
    # they are implicitly tied to pixels-per-mm. Re-anchor them to physical
    # units (the calibration tile carries a mm scale) in a 3.x variant at
    # that point, rather than editing this one in place.
    "2.0": """Tu examines une photo d'un échantillon de larves de mouche soldat noire \
(Hermetia illucens) mélangées à de la MEO (matières étrangères organiques, résidu d'élevage \
aussi appelé frass), pour estimer le niveau de contamination visible.

L'échantillon est présenté dans un plat circulaire qui a été rempli puis arasé, de sorte que \
la surface visible est plane et couvre entièrement l'intérieur du plat. Estime quelle \
proportion de cette surface est occupée par de la MEO plutôt que par des larves.

Ignore complètement tout ce qui se trouve à l'extérieur du plat -- le comptoir, le rebord du \
plat lui-même, son ombre, ainsi que toute carte ou mire de référence pouvant apparaître dans \
l'image. Rien de cela ne fait partie de l'échantillon. Évalue uniquement l'intérieur du plat.

Attention à ne pas confondre une prépupe avec de la MEO/frass. En approchant le stade de \
prépupe, la larve fonce considérablement -- brun foncé à presque noir -- et peut, par sa \
seule couleur, ressembler à un amas de frass. Une prépupe reste une larve normale du produit, \
PAS une matière étrangère : avant de compter un élément foncé comme de la MEO, regarde sa \
forme (silhouette allongée et segmentée d'une larve, souvent encore reconnaissable même très \
foncée) plutôt que sa seule teinte.

Attention aussi aux fragments de larves brisées ou écrasées -- fréquents lors de la \
manipulation, et normaux, PAS de la contamination. Une larve cassée expose une chair pâle, \
blanchâtre ou crème, nettement différente de la couleur brun-à-noir du frass et de la MEO. \
Avant de compter un petit fragment comme de la MEO, vérifie sa couleur : un fragment pâle ou \
de la même teinte que les larves intactes est probablement un morceau de larve brisée, pas de \
la matière étrangère. Ne compte un petit fragment comme de la MEO que s'il est clairement plus \
foncé (brun à noir) que la chair d'une larve.

Ignore aussi les fragments minuscules -- poussière fine, résidu pulvérulent, grains isolés de \
la taille d'un point -- qui ne forment pas un amas ou une particule clairement visible \
individuellement. Seule une matière qui se distingue nettement à l'œil nu, comme le ferait un \
inspecteur qui jette un coup d'œil rapide au plat (pas un examen à la loupe), doit compter \
dans l'estimation de densité.

Ne tente PAS de compter les particules individuelles -- donne une impression visuelle globale \
de densité, comme le ferait une personne qui regarde rapidement le plat.

Réponds EXACTEMENT selon ce format, rien d'autre avant ou après :

BANDE: [une seule valeur parmi : <1%, 1-3%, 3-7%, 7-10%, 10-14%, >14%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [liste séparée par virgules, uniquement parmi : prepupes, fragments_ecrases, poussiere, \
densite_reelle, autre -- les éléments qui ont RÉELLEMENT influencé ce choix de bande sur cette \
photo précise, pas une liste générique]
JUSTIFICATION: [une phrase, ce qui a motivé ce choix]""",

    # Candidate -- NOT the default. Identical to "2.0" except for the
    # low-end anchor paragraph carried over from "1.4". That instruction was
    # fitted to old-rig, scatter-method data, so whether it still earns its
    # place under the dish method is an open question rather than a settled
    # one. Run "2.0" and "2.1" together on the same capture (they cost about
    # one call's wall-clock time between them, see the asyncio.gather below)
    # and let the comparison table decide, same rule as every candidate
    # before it.
    "2.1": """Tu examines une photo d'un échantillon de larves de mouche soldat noire \
(Hermetia illucens) mélangées à de la MEO (matières étrangères organiques, résidu d'élevage \
aussi appelé frass), pour estimer le niveau de contamination visible.

L'échantillon est présenté dans un plat circulaire qui a été rempli puis arasé, de sorte que \
la surface visible est plane et couvre entièrement l'intérieur du plat. Estime quelle \
proportion de cette surface est occupée par de la MEO plutôt que par des larves.

Ignore complètement tout ce qui se trouve à l'extérieur du plat -- le comptoir, le rebord du \
plat lui-même, son ombre, ainsi que toute carte ou mire de référence pouvant apparaître dans \
l'image. Rien de cela ne fait partie de l'échantillon. Évalue uniquement l'intérieur du plat.

Attention à la zone basse de l'échelle en particulier : à peine quelques petites taches ou \
grains isolés et bien espacés sur la surface correspond typiquement à <1% ou 1-3%, PAS à \
3-7%. Réserve 3-7% aux cas où la matière étrangère forme un ensemble de taches ou d'amas \
visibles sur une bonne partie de la surface -- pas seulement quelques points épars ici et là.

Attention à ne pas confondre une prépupe avec de la MEO/frass. En approchant le stade de \
prépupe, la larve fonce considérablement -- brun foncé à presque noir -- et peut, par sa \
seule couleur, ressembler à un amas de frass. Une prépupe reste une larve normale du produit, \
PAS une matière étrangère : avant de compter un élément foncé comme de la MEO, regarde sa \
forme (silhouette allongée et segmentée d'une larve, souvent encore reconnaissable même très \
foncée) plutôt que sa seule teinte.

Attention aussi aux fragments de larves brisées ou écrasées -- fréquents lors de la \
manipulation, et normaux, PAS de la contamination. Une larve cassée expose une chair pâle, \
blanchâtre ou crème, nettement différente de la couleur brun-à-noir du frass et de la MEO. \
Avant de compter un petit fragment comme de la MEO, vérifie sa couleur : un fragment pâle ou \
de la même teinte que les larves intactes est probablement un morceau de larve brisée, pas de \
la matière étrangère. Ne compte un petit fragment comme de la MEO que s'il est clairement plus \
foncé (brun à noir) que la chair d'une larve.

Ignore aussi les fragments minuscules -- poussière fine, résidu pulvérulent, grains isolés de \
la taille d'un point -- qui ne forment pas un amas ou une particule clairement visible \
individuellement. Seule une matière qui se distingue nettement à l'œil nu, comme le ferait un \
inspecteur qui jette un coup d'œil rapide au plat (pas un examen à la loupe), doit compter \
dans l'estimation de densité.

Ne tente PAS de compter les particules individuelles -- donne une impression visuelle globale \
de densité, comme le ferait une personne qui regarde rapidement le plat.

Réponds EXACTEMENT selon ce format, rien d'autre avant ou après :

BANDE: [une seule valeur parmi : <1%, 1-3%, 3-7%, 7-10%, 10-14%, >14%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [liste séparée par virgules, uniquement parmi : prepupes, fragments_ecrases, poussiere, \
densite_reelle, autre -- les éléments qui ont RÉELLEMENT influencé ce choix de bande sur cette \
photo précise, pas une liste générique]
JUSTIFICATION: [une phrase, ce qui a motivé ce choix]""",
}

# Which variant /health reports and which /azure-band-test runs when the
# caller doesn't ask for a specific comparison set -- keeps single-variant
# callers (the normal "Analyser avec Caliban" button) fully backward
# compatible, at the same cost as before, with no behavior change unless
# someone actively opts into comparing variants.
#
# History (winners get folded back in as the next default; see the
# comment on BAND_PROMPT_VARIANTS above for how candidates are added):
#
# v1.2: removed the one-directional "if you see stacking, lean toward a
# higher band" instruction from v1.1. That instruction had no
# corresponding downward pull, and real samples almost never spread into
# a perfectly flat single layer -- meaning it likely fired on most
# photos, always in the same direction. Suspected root cause of a
# consistent over-read, especially pronounced at the low end (1-3%),
# where the narrow band width means even a small nudge crosses a
# boundary.
#
# v1.3: v1.2 did NOT fix the over-read -- checked against
# vision_band_estimates for real (non-training) lots with a known lab
# ME%: v1.1 over-estimated 12/12 real-lot samples (avg +3.6 band-midpoint
# points over real ME%), v1.2 still over-estimated 24/32 (75%, avg +2.6),
# frequently at "Élevée" confidence while wrong. Rather than add another
# blind directional nudge (the exact mistake v1.2 just corrected for),
# this version targeted two specific, hypothesized causes instead:
# prepupae plausibly read as frass/MEO by color alone (added a
# shape-over-color instruction), and no prior instruction excluding fine
# dust/tiny fragments from the density impression (added one).
#
# A full week of real-lot v1.3 results, split by batch, turned out to be
# genuinely unstable rather than settled in either direction: 2026-08-10
# (n=11) averaged -1.3 (73% under), 2026-08-13 (n=12) averaged +2.76
# (75% over -- almost exactly the pre-v1.3 severity). Several lots were
# also tested twice on the same day with wildly different bands on the
# same real value (e.g. one lot read as both "1-3%" and "7-10%"),
# pointing at real call-to-call non-determinism on top of whatever the
# wording says -- see CALIBAN_SEED below, added alongside this candidate
# specifically to reduce that. Within the 08-13 over-reads, a clear
# pattern held: real values under ~2% were repeatedly read as "3-7%"
# instead of "<1%"/"1-3%". "1.4" targets that one boundary; it is NOT
# yet the default -- run it alongside "1.3" (see `variants` param on
# /azure-band-test) and let the comparison table actually decide before
# promoting it.
#
# "1.5": added before any 1.4 comparison data came back, on a
# failure mode the operator identified directly from handling the
# physical product rather than from vision_band_estimates: crushed/
# broken larvae fragments (routine handling damage) suspected as the
# single biggest driver of over-reads. Built on "1.4" rather than "1.3"
# so running all three together separates two questions at once -- did
# the low-end anchor help, and does the crushed-larvae color gate help
# further on top of it. Also not the default; same "prove it in the
# comparison table before promoting" rule applies.
#
# v2.0: promoted straight to default WITHOUT a comparison against 1.3,
# which is a deliberate exception to the rule above rather than an
# oversight. The sample presentation changed physically -- filled and
# levelled circular dish instead of a scattered single layer -- and 1.3
# through 1.5 all open by describing empty tray space around the sample
# to justify how the denominator should be chosen. That arrangement no
# longer exists, so running 1.3 against a dish photo isn't a fair
# comparison, it's a wrong answer produced from a false premise. There is
# no meaningful A/B to run; the comparisons that matter from here are
# among the 2.x variants.
#
# The 1.x keys stay in the dict so existing vision_band_estimates rows
# remain interpretable against the prompt_hash that actually produced
# them. Note also that the few-shot reference photos in
# vision_reference_images were captured with the old scatter method --
# they need re-shooting on the dish before they help rather than hurt,
# since grounding a dish photo against scatter-method references is worse
# than sending no references at all.
# 3.1 is 3.0 with two changes, derived rather than retyped.
#
# Deriving is safe here precisely because of the rule these variants already
# follow: a prompt is never rewritten in place, a new label is added instead.
# So 3.0's text cannot change under 3.1's feet. The asserts below are the
# guard that matters -- a substitution that silently stopped matching would
# otherwise leave 3.1 identical to 3.0 while still claiming to be different,
# and PROMPT_HASHES would faithfully record two labels for one prompt.
#
# Change one: four wider bands instead of six. Fewer, wider buckets are a
# question the model can actually answer, and the 3-7 / 7-10 confusion has
# been the standing complaint for months. The resolution given up is recovered
# by averaging rotations into estimate_pct, which is continuous and finer than
# any band -- before that machinery existed this would have been a pure loss.
#
# Change two: a bulk density estimate. Worth being clear about what this can
# and cannot be. A top-down photo of a thin single layer carries very little
# direct information about bulk density, which is a property of packing and
# moisture, and a scattered monolayer shows neither. What the photo does carry
# is correlates -- larva size, how plump or shrivelled they look, the fraction
# of fines. So this is not asking the model to measure density. It is asking
# it to be a consistent instrument whose readings can be regressed against
# measured values later. Consistency is the property to test first: if the
# spread across rotations is wide, there is nothing to calibrate and the idea
# should be dropped rather than tuned.
_P30 = BAND_PROMPT_VARIANTS["3.0"]

_P31 = _P30.replace(
    "BANDE: [a single value among : <1%, 1-3%, 3-7%, 7-10%, 10-14%, >14%]",
    "BANDE: [a single value among : <3%, 3-8%, 8-13%, >13%]",
).replace(
    """Be careful at the low end of the scale. A few small isolated specks, well separated across the sample, is typically <1% or 1-3% -- not 3-7%. Reserve 3-7% for when foreign matter forms visible specks or clumps across a good part of the sample area, while the larvae still plainly dominate. Above that, at 7-10%, dark material is a continuous presence rather than scattered incidents: there is some in every part of the sample, and it begins to read as a component of the mixture rather than as debris within it.""",
    """Be careful at the low end of the scale. A few small isolated specks, well separated across the sample, is <3%. Reserve 3-8% for when foreign matter forms visible specks or clumps across a good part of the sample area, while the larvae still plainly dominate. At 8-13% dark material is a continuous presence rather than scattered incidents: there is some in every part of the sample, and it reads as a component of the mixture rather than as debris within it.""",
).replace(
    "JUSTIFICATION: [one sentence, in French, what drove this choice]",
    """DENSITE: [your best estimate of the sample's bulk density in grams per litre, as a single number or a range like 480-560. Judge it from what the photograph shows: the size of the larvae, how plump or shrivelled they look, and how much fine material is present. Give a number even when uncertain -- consistency between photographs matters more here than absolute accuracy.]
JUSTIFICATION: [one sentence, in French, what drove this choice]""",
)

assert _P31.count("<3%, 3-8%, 8-13%, >13%") == 1, "band substitution did not apply"
assert "3-7%" not in _P31, "old band boundaries survived into 3.1"
assert "DENSITE:" in _P31, "density substitution did not apply"
BAND_PROMPT_VARIANTS["3.1"] = _P31

# 3.2 is 3.1 with the density question anchored to real numbers.
#
# 3.1 asked for a bulk density and got 550 g/L against a true range of roughly
# 140-250. That is not the model reading the photograph badly -- it is the
# model having no idea what this product weighs and reaching for a generic
# biomass prior. Dried BSF larvae are unusually light: they are hollow, dry and
# irregular, so they pack with a great deal of air between them, and a number
# an order of magnitude closer to wet grain is what any general prior would
# produce.
#
# So the fix is to say what the product weighs, in the only terms a photograph
# can support -- what the larvae look like. The two anchors are measured
# values, and they run in the direction that surprises people: larger, puffier
# larvae give a LOWER bulk density, because rounded bodies nest badly and trap
# air; small flat ones pack closer and read higher. Stating the direction
# matters as much as the numbers, since a model told only "140 to 250" has no
# way to know which end a puffy tray belongs at.
#
# The hard bound at the end is doing real work. Without it, an anchored range
# is a suggestion the model is free to leave, which is exactly what it did.
_P32 = _P31.replace(
    """DENSITE: [your best estimate of the sample's bulk density in grams per litre, as a single number or a range like 480-560. Judge it from what the photograph shows: the size of the larvae, how plump or shrivelled they look, and how much fine material is present. Give a number even when uncertain -- consistency between photographs matters more here than absolute accuracy.]""",
    """DENSITE: [the sample's bulk density in grams per litre. Anchor it on these two measured references from this same rig:
  - a tray of mostly large, puffy, well-rounded larvae is about 140 g/L
  - a tray of mostly small, flat, shrivelled larvae is about 250 g/L
Note the direction: bigger and puffier means LOWER density, because rounded larvae nest badly and trap air between them, while small flat ones pack closer together. Fine material fills the gaps and raises it further. Almost every sample falls between these two references. Do not answer outside 120-280 g/L unless the tray looks clearly more extreme than either description, and say so in the justification if you do. Give a single number or a narrow range.]""",
)

assert _P32 != _P31, "density anchoring did not apply"
assert "140 g/L" in _P32 and "250 g/L" in _P32, "anchors missing from 3.2"
assert "480-560" not in _P32, "the old unanchored example survived"
BAND_PROMPT_VARIANTS["3.2"] = _P32


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

# Hidden from the variant picker without being deleted. Archived variants stay
# runnable when named explicitly, so a past image can still be re-scored
# against the prompt that produced it -- but they no longer clutter a UI whose
# job is now comparing candidates for the current method.
#
# 1.3 and 1.4 are superseded by 1.5 on the same method. 2.0 and 2.1 describe
# the filled dish and are archived with it; if that method is ever revived they
# are ready and unmodified.
ARCHIVED_PROMPT_VARIANTS = {"1.3", "1.4", "2.0", "2.1"}

# Which variants get the few-shot reference photos attached. Deliberately
# an explicit opt-IN list rather than an opt-out one, so any future variant
# defaults to prompt-only unless someone consciously decides otherwise.
#
# 2.x runs without references on purpose, and this is not the same thing as
# the reference table happening to be empty. Making it a property of the
# variant means the behaviour is pinned to the prompt_version recorded on
# every row: if reference photos are added back to vision_reference_images
# later, 2.0 does NOT silently change what it sends, and nobody has to
# explain a step-change in results that no prompt_hash accounts for.
#
# Reasons for dropping them here: there's no clean-larvae source available
# to shoot a new set against the dish; the scatter-method set that exists
# would be actively misleading grounding for dish photos; results were
# reasonable without references under the scatter method anyway; and
# reference photos are harder to control under the fill-and-level method,
# where what's visible is a levelled surface rather than the whole sample.
VARIANTS_USING_REFERENCES = {"1.3", "1.4", "1.5"}

# Computed automatically from the actual prompt text every time this
# module loads -- guaranteed accurate even if a variant's label/text
# ever drift out of sync. This is the real, tamper-proof way to know
# whether two results actually came from the same prompt.
PROMPT_HASHES = {
    label: hashlib.md5(text.encode("utf-8")).hexdigest()[:8]
    for label, text in BAND_PROMPT_VARIANTS.items()
}

# Kept as aliases (rather than rewriting /health and every caller) --
# both point at whichever variant is current default.
CALIBAN_PROMPT_VERSION = DEFAULT_PROMPT_VARIANT
PROMPT_HASH = PROMPT_HASHES[DEFAULT_PROMPT_VARIANT]


def parse_density(raw):
    """A bulk density in g/L from whatever the model wrote.

    Accepts a single number or a range, because the prompt permits both and
    insisting on one shape would throw away answers over formatting. A range
    collapses to its midpoint: the width is the model's hedging rather than a
    measurement, and the spread that matters is the one across rotations,
    which is measured rather than declared.

    Bounds are deliberately far wider than the real range, which measures
    about 140-250 g/L for this product -- dried larvae are hollow and irregular
    and pack with a lot of air. The filter is here to catch unit slips and
    nonsense, not to enforce the answer: a model reading 550 when the truth is
    200 is the single most useful thing this data can tell us, and quietly
    dropping it would hide the error the calibration exists to find.
    """
    numbers = re.findall(r"\d+(?:[.,]\d+)?", raw or "")
    if not numbers:
        return None
    values = [float(n.replace(",", ".")) for n in numbers[:2]]
    value = sum(values) / len(values)

    # The prompt asks for g/L, but kg/L is the more natural unit for anyone
    # who thinks in specific gravity, and a model that answers "0.52 kg/L" has
    # given a perfectly good answer in the wrong unit. Rescuing it beats
    # dropping it silently and finding a hole in the data later.
    if "kg" in (raw or "").lower() and value < 10.0:
        value *= 1000.0

    if not 20.0 <= value <= 2000.0:
        return None
    return round(value, 1)


def parse_band_response(text):
    """Extracts the structured band/confidence fields from BAND_PROMPT's
    response -- falls back to returning the raw text untouched if the
    model didn't follow the exact format, rather than silently dropping
    a real answer that just wasn't formatted as expected."""
    band = confidence = justification = None
    factors = None
    density = None
    for line in text.strip().splitlines():
        if line.upper().startswith("BANDE:"):
            band = line.split(":", 1)[1].strip()
        elif line.upper().startswith("CONFIANCE:"):
            confidence = line.split(":", 1)[1].strip()
        elif line.upper().startswith("FACTEURS:"):
            raw_factors = line.split(":", 1)[1].strip()
            factors = [f.strip() for f in raw_factors.split(",") if f.strip()] or None
        elif line.upper().startswith("DENSITE:"):
            density = parse_density(line.split(":", 1)[1])
        elif line.upper().startswith("JUSTIFICATION:"):
            justification = line.split(":", 1)[1].strip()
    return {
        "band": band,
        "confidence": confidence,
        "factors": factors,
        "justification": justification,
        "density_est": density,
        "raw": text,
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
}

# Which scale each variant speaks. Absent means "standard", so every existing
# variant keeps its meaning without being listed.
VARIANT_BAND_SCALE = {"3.1": "coarse", "3.2": "coarse"}

# Flattened for lookup by label. Labels are unique across scales, which is not
# an accident worth relying on silently -- assert it, because a collision would
# make a band from one scale silently resolve to the other's midpoint.
BAND_MIDPOINTS = {}
for _scale in BAND_SCALES.values():
    for _label, _lower, _mid in _scale:
        assert _label not in BAND_MIDPOINTS, f"duplicate band label {_label}"
        BAND_MIDPOINTS[_label] = _mid

# How far a predicted band's midpoint has to be from the known real ME%
# before a row gets auto-flagged for human review. 3 points is about one
# band-width -- wide enough that boundary-adjacent misses (which are
# somewhat expected/tolerable) don't flood the review queue, narrow
# enough to still catch the "way off" cases that are actually worth
# looking at a photo for.
OUTLIER_THRESHOLD_PTS = 3.0


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


def is_outlier(band, real_pct_value):
    if real_pct_value is None:
        return False
    midpoint = BAND_MIDPOINTS.get((band or "").strip())
    if midpoint is None:
        return False
    return abs(midpoint - real_pct_value) >= OUTLIER_THRESHOLD_PTS


@app.post("/claude-detect")
async def claude_detect(
    file: UploadFile = File(...),
    lot_number: str = Form(""),
    operator: dict = Depends(get_current_operator),
):
    if not ANTHROPIC_API_KEY:
        raise HTTPException(status_code=503, detail="ANTHROPIC_API_KEY n'est pas configurée sur ce service.")

    contents = await file.read()
    media_type = file.content_type or "image/jpeg"
    b64_image = base64.b64encode(contents).decode("utf-8")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    start = time.time()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64_image}},
                {"type": "text", "text": CLAUDE_VISION_PROMPT},
            ],
        }],
    )
    elapsed_ms = int((time.time() - start) * 1000)

    text = "".join(block.text for block in response.content if block.type == "text")
    return {
        "lot_number": lot_number,
        "analysis": text,
        "inference_time_ms": elapsed_ms,
        "model": "claude-sonnet-4-6",
    }


# ── Azure AI Foundry (GPT-4o) vision test endpoint ────────────────────
# Same evaluation-tool status as /claude-detect, same prompt verbatim --
# deliberately identical, so a photo run through both gives a genuinely
# fair, apples-to-apples comparison rather than two different questions
# being asked of two different models. Also does not persist to
# vision_inspections, for the same reason as /claude-detect.
#
# Requires these Render environment variables (from your Azure AI Foundry
# / Azure OpenAI resource -- portal.azure.com or ai.azure.com):
#   AZURE_OPENAI_ENDPOINT     the bare resource URL only, no path after it.
#                             For newer AI Foundry resources this looks like
#                             https://<resource>.services.ai.azure.com --
#                             NOT the "Project endpoint" (.../api/projects/...)
#                             or the newer Responses API URL
#                             (.../openai/v1/responses) shown elsewhere in
#                             the Foundry portal. Confirmed directly against
#                             Microsoft's own current documentation for this
#                             resource type after a real 404 traced to using
#                             the wrong one of these three.
#   AZURE_OPENAI_API_KEY
#   AZURE_OPENAI_DEPLOYMENT   the name YOU gave the deployed model when
#                             you deployed gpt-4o in the Foundry portal
#                             (not necessarily "gpt-4o" itself -- this is
#                             a deployment name you chose, confirm it in
#                             Foundry > Models + endpoints)
#
# Verified directly against the real, installed openai package
# (AzureOpenAI's constructor and chat.completions.create's parameters)
# before writing this -- the actual call against a real Azure resource
# could not be tested from this environment (no Azure credentials
# available here), so treat the first real call as the first real test
# of this specific piece, same caveat as the SharePoint integration.

from openai import AzureOpenAI, BadRequestError

AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")

# Fixed, arbitrary seed for /azure-band-test's calls -- paired with
# temperature=0 (see call_variant), this is meant to make repeat calls on
# the same photo reproducible. Added after a week of real v1.3 traffic
# showed the same lot tested twice on the same day sometimes came back
# with wildly different bands (e.g. one lot read as both "1-3%" and
# "7-10%") -- sampling randomness (the default temperature is 1.0) is a
# plausible contributor to that, independent of anything the prompt text
# says. Azure/OpenAI's seed support is "best-effort," not a hard
# guarantee of identical output -- this reduces variance, it doesn't
# eliminate it.
CALIBAN_SEED = 20260101

_azure_client = None


def get_azure_client():
    global _azure_client
    if _azure_client is None:
        for name, val in [("AZURE_OPENAI_ENDPOINT", AZURE_OPENAI_ENDPOINT), ("AZURE_OPENAI_API_KEY", AZURE_OPENAI_API_KEY), ("AZURE_OPENAI_DEPLOYMENT", AZURE_OPENAI_DEPLOYMENT)]:
            if not val:
                raise HTTPException(status_code=503, detail=f"{name} n'est pas configurée sur ce service.")
        _azure_client = AzureOpenAI(
            azure_endpoint=AZURE_OPENAI_ENDPOINT,
            api_key=AZURE_OPENAI_API_KEY,
            api_version=AZURE_OPENAI_API_VERSION,
        )
    return _azure_client


_component_test_ids = "__unset__"  # sentinel distinct from None, so a genuine "not found" isn't re-queried every request


def get_me_pct_component_ids():
    """ME% is never stored as its own test_results row -- confirmed
    directly against real data, where a fully-tested lot had
    me_organic_wt and bulk_density but no me_pct entry at all. SGSC
    computes the percentage itself (me_organic_wt / bulk_density * 100),
    same formula already used elsewhere in the app (PeriodTab). This
    looks up the two real, stored component tests instead of searching
    for a percentage that structurally doesn't exist as a row."""
    global _component_test_ids
    if _component_test_ids == "__unset__":
        resp = supabase.table("test_definitions").select("id, code").in_("code", ["me_organic_wt", "bulk_density"]).execute()
        ids = {row["code"]: row["id"] for row in (resp.data or [])}
        _component_test_ids = (ids.get("me_organic_wt"), ids.get("bulk_density")) if ids else None
    return _component_test_ids


@app.post("/azure-detect")
async def azure_detect(
    file: UploadFile = File(...),
    lot_number: str = Form(""),
    operator: dict = Depends(get_current_operator),
):
    client = get_azure_client()
    contents = await file.read()
    media_type = file.content_type or "image/jpeg"
    b64_image = base64.b64encode(contents).decode("utf-8")

    start = time.time()
    response = client.chat.completions.create(
        model=AZURE_OPENAI_DEPLOYMENT,
        max_completion_tokens=1000,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": CLAUDE_VISION_PROMPT},
                {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64_image}"}},
            ],
        }],
    )
    elapsed_ms = int((time.time() - start) * 1000)

    text = response.choices[0].message.content or ""
    return {
        "lot_number": lot_number,
        "analysis": text,
        "inference_time_ms": elapsed_ms,
        "model": f"azure/{AZURE_OPENAI_DEPLOYMENT}",
    }


# ── Standalone band-estimate validation tool ──────────────────────────
# Deliberately outside the Supabase-authenticated app entirely -- this is
# a quick validation experiment, not a production feature, so it uses the
# same shared-secret pattern as the Power Automate endpoint rather than
# requiring a logged-in SGSC session for what's meant to be a fast,
# throwaway test against real lab ME% values.
#
# Requires a new env var on Render: VALIDATION_API_KEY (any long random
# string you choose -- separate from POWER_AUTOMATE_API_KEY so revoking
# one doesn't affect the other).

# ── Standalone band-estimate validation tool ──────────────────────────
# Originally a quick, standalone experiment outside SGSC's auth entirely
# -- now promoted to a real, role-gated feature. Uses the same Supabase
# JWT verification as every other authenticated endpoint (get_current_operator),
# plus a role check matching the app's own hierarchy (user_profiles.role),
# rather than the standalone VALIDATION_API_KEY used during initial testing.

ROLE_HIERARCHY = {"viewer": 0, "qc": 1, "qa": 2, "qa_director": 3, "osa": 4}


def require_role(min_role: str):
    """Returns a FastAPI dependency that verifies the caller's Supabase
    session AND that their user_profiles.role meets the given minimum,
    matching the same role names and ordering used throughout the rest
    of the app (roleGte() client-side, role_gte() in RLS policies)."""
    def dependency(operator: dict = Depends(get_current_operator)):
        profile_resp = supabase.table("user_profiles").select("role").eq("id", operator["id"]).single().execute()
        role = (profile_resp.data or {}).get("role", "viewer")
        if ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY.get(min_role, 0):
            raise HTTPException(status_code=403, detail=f"Accès refusé -- rôle {min_role} ou supérieur requis.")
        return operator
    return dependency


def band_slug(band):
    """Turns a predicted band like '3-7%' into a clean identifier
    fragment like '3_7'. Falls back safely for an unstructured or
    missing band, rather than let a stray character or space leak into
    an auto-generated ID."""
    if not band:
        return "inconnu"
    s = band.replace("%", "").replace("<", "lt").replace(">", "gt").replace("-", "_")
    s = re.sub(r"[^a-zA-Z0-9_]", "_", s)
    return s[:20]


REFERENCE_BUCKET = "vision-reference-images"
_reference_cache = None

# Separate bucket from REFERENCE_BUCKET on purpose -- these are routine
# production captures (high volume, no curation), not the small,
# hand-picked few-shot set. Keeping them apart means a retention/cleanup
# policy can be applied to this bucket later without touching reference
# photos. Must be created once in the Supabase dashboard (Storage --
# same private visibility as vision-reference-images) before this is used.
BAND_TEST_CAPTURE_BUCKET = "vision-band-test-captures"


def clear_reference_cache():
    """Called after a new reference photo is captured, so it's picked up
    on the very next request rather than waiting for a container restart
    -- the whole reason this moved off the old baked-into-the-image
    approach in the first place."""
    global _reference_cache
    _reference_cache = None


def get_reference_images(category="meo_density"):
    """Loads reference images for the given category from Supabase
    (metadata table + Storage bucket), caching the result until
    explicitly invalidated by clear_reference_cache(). Returns an empty
    list gracefully if none exist yet for this category, so the tool
    keeps working exactly as before until real reference photos are
    actually captured."""
    global _reference_cache
    if _reference_cache is None:
        _reference_cache = {}
        try:
            rows = supabase.table("vision_reference_images").select("*").execute().data or []
        except Exception:
            rows = []
        by_category = {}
        for row in rows:
            by_category.setdefault(row["category"], []).append(row)
        for cat, items in by_category.items():
            loaded = []
            for item in items:
                try:
                    file_bytes = supabase.storage.from_(REFERENCE_BUCKET).download(item["storage_path"])
                    b64 = base64.b64encode(file_bytes).decode("utf-8")
                    loaded.append({**item, "b64": b64})
                except Exception:
                    continue  # a single missing/corrupt file shouldn't block the rest
            by_category[cat] = loaded
        _reference_cache = by_category
    return _reference_cache.get(category, [])


@app.post("/azure-band-test")
async def azure_band_test(
    file: UploadFile = File(...),
    lot_number: str = Form(""),
    real_pct: str = Form(""),
    is_training: bool = Form(False),
    variants: str = Form(""),
    repeats: int = Form(1),
    operator: dict = Depends(require_role("qc")),
):
    # Comma-separated BAND_PROMPT_VARIANTS keys, e.g. "1.3,1.4a,1.4b" --
    # empty/omitted keeps the old single-call behavior (DEFAULT_PROMPT_VARIANT
    # only), so existing callers see no change in behavior or cost unless
    # they actively opt into a comparison.
    requested_variants = [v.strip() for v in variants.split(",") if v.strip()] or [DEFAULT_PROMPT_VARIANT]
    unknown = [v for v in requested_variants if v not in BAND_PROMPT_VARIANTS]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Variante(s) de prompt inconnue(s) : {', '.join(unknown)}. "
                   f"Disponibles : {', '.join(BAND_PROMPT_VARIANTS)}.",
        )

    client = get_azure_client()
    contents = await file.read()
    media_type = file.content_type or "image/jpeg"
    b64_image = base64.b64encode(contents).decode("utf-8")

    # Repeats are ROTATIONS, not re-samples.
    #
    # The calls below run at temperature 0 with a fixed seed, deliberately, so
    # asking the same question twice returns the same answer -- re-sampling
    # would measure nothing. Rotating the photo genuinely changes the input
    # while leaving the correct answer untouched, because a scattered tray has
    # no orientation. The estimates are therefore independent, and their spread
    # measures sensitivity to how the material happened to lie rather than
    # sampling noise.
    #
    # Averaging them also breaks the band quantisation. One call can only
    # answer to the nearest band; four answering 3-7, 3-7, 7-10, 3-7 average to
    # 5.9%, a finer distinction than any single call can express. That matters
    # most exactly where the prompt is weakest -- the 3-7 / 7-10 boundary.
    # Eight transforms, not ten, and every one of them lossless. Right angles
    # and mirrors move pixels without resampling them; any other angle
    # interpolates, softens exactly the fine dark detail being judged, and
    # leaves blank corners the model has to be told to ignore. A ninth
    # presentation would therefore differ from the first eight in image quality
    # as well as orientation, which confounds the very thing this measures.
    #
    # Ordered so that a small number of repeats spends them well: 0 and 180
    # are the most different pair, then the other two right angles, then the
    # mirrors. Asking for 2 should not get you two nearly identical views.
    #
    # Mirrors count as genuine repeats here. A mirrored tray is the same
    # sample presented differently, and the answer has no business changing --
    # so disagreement across mirrors is the same signal as disagreement across
    # rotations.
    TRANSFORMS = [
        (0, False), (180, False), (90, False), (270, False),
        (0, True), (180, True), (90, True), (270, True),
    ]
    repeats = max(1, min(len(TRANSFORMS), repeats))
    transforms = TRANSFORMS[:repeats]

    def _key(angle, mirror):
        return f"{angle}m" if mirror else str(angle)

    rotated_b64 = {"0": b64_image}
    if repeats > 1:
        from PIL import Image

        original = Image.open(io.BytesIO(contents)).convert("RGB")
        for angle, mirror in transforms[1:]:
            # expand=True keeps the whole frame at 90/270 on a non-square
            # image; cropping instead would hand each rotation a different
            # sample, which is precisely what must not vary.
            img = original.rotate(angle, expand=True)
            if mirror:
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=95)
            rotated_b64[_key(angle, mirror)] = base64.b64encode(buf.getvalue()).decode("utf-8")

    # Save the actual captured photo, once per call (same photo for every
    # variant being compared) -- best-effort, since a storage hiccup
    # should never block the analysis the operator is waiting on. Without
    # this, an outlier row is just a number and a sentence forever; with
    # it, any flagged row can actually be looked at later to see what the
    # model saw. Root motivation: "we need a concrete reason instead of
    # guessing" for the recurring over-read investigation.
    capture_storage_path = None
    try:
        ext = os.path.splitext(file.filename or "")[1] or ".jpg"
        capture_storage_path = f"captures/{uuid.uuid4().hex}{ext}"
        upload_resp = supabase.storage.from_(BAND_TEST_CAPTURE_BUCKET).upload(
            capture_storage_path, contents, file_options={"content-type": media_type}
        )
        if not upload_resp:
            capture_storage_path = None
    except Exception as e:
        capture_storage_path = None
        print(f"[band-test capture upload failed] {type(e).__name__}: {e}")

    # Few-shot grounding: real reference photos with known values, judged
    # alongside the new photo rather than asked to reason about density
    # in the abstract. Shared across every variant that opts into them (see
    # VARIANTS_USING_REFERENCES): same references, same photo, only the
    # instructions text differs between calls.
    #
    # Loaded lazily -- if none of the requested variants use references
    # (the normal case now that 2.x is default), skip the Supabase Storage
    # round-trip that get_reference_images does on a cold container rather
    # than downloading and base64-encoding files nothing will send.
    references = (
        get_reference_images("meo_density")
        if any(v in VARIANTS_USING_REFERENCES for v in requested_variants)
        else []
    )

    def call_variant(variant_label, angle=0, mirror=False):
        content = [{"type": "text", "text": BAND_PROMPT_VARIANTS[variant_label]}]
        # Per-variant, not per-request: comparing a reference-using variant
        # against a prompt-only one on the same photo has to send different
        # payloads, or the comparison isn't measuring what it claims to.
        variant_refs = references if variant_label in VARIANTS_USING_REFERENCES else []
        if variant_refs:
            content.append({
                "type": "text",
                "text": "\n\nVoici des photos de référence avec leur pourcentage réel connu de "
                        "MEO, pour calibrer ton estimation :",
            })
            for ref in variant_refs:
                label = f"Référence -- {ref['real_pct']}% MEO réel"
                if ref.get("description"):
                    label += f" ({ref['description']})"
                content.append({"type": "text", "text": label + " :"})
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref['b64']}"}})
            content.append({"type": "text", "text": "\n\nMaintenant, voici la photo à évaluer :"})
        content.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{rotated_b64[_key(angle, mirror)]}"}})

        start = time.time()
        try:
            response = client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT,
                max_completion_tokens=200,
                temperature=0,
                seed=CALIBAN_SEED,
                messages=[{"role": "user", "content": content}],
            )
        except BadRequestError:
            # Some deployed models (newer reasoning-tier ones especially)
            # reject sampling params like temperature/seed outright rather
            # than ignore them -- retry once without them instead of
            # taking down the whole comparison over one incompatible
            # variant. Whether this path is ever actually hit depends on
            # whatever model AZURE_OPENAI_DEPLOYMENT currently points at.
            response = client.chat.completions.create(
                model=AZURE_OPENAI_DEPLOYMENT,
                max_completion_tokens=200,
                messages=[{"role": "user", "content": content}],
            )
        elapsed_ms = int((time.time() - start) * 1000)
        text = response.choices[0].message.content or ""
        parsed = parse_band_response(text)
        parsed["inference_time_ms"] = elapsed_ms
        parsed["model"] = f"azure/{AZURE_OPENAI_DEPLOYMENT}"
        parsed["reference_count"] = len(variant_refs)
        parsed["prompt_version"] = variant_label
        parsed["prompt_hash"] = PROMPT_HASHES[variant_label]
        parsed["rotation"] = _key(angle, mirror)
        return parsed


    def aggregate(variant_label, runs):
        """Collapse a variant's rotations into one result.

        Reports a continuous estimate alongside the band, because the mean of
        several band midpoints carries information no single band can. The
        reported band is then whichever one that mean falls in -- so band and
        estimate never contradict each other, which picking the modal band
        separately could allow.

        Spread is the honest uncertainty here: it is how much the answer moved
        when nothing about the sample changed. A tight spread earns confidence
        in a way the model's own self-reported confidence does not.
        """
        primary = next((r for r in runs if r.get("rotation") == "0"), runs[0])
        midpoints = [BAND_MIDPOINTS[r["band"]] for r in runs
                     if r.get("band") in BAND_MIDPOINTS]

        # Density gets the same treatment as the band, and for the same
        # reason: nothing about the sample changed between rotations, so the
        # spread is how much the answer moved for no reason. That spread is
        # the first thing to look at -- if it is wide there is nothing to
        # calibrate against measured density, and the idea should be dropped
        # rather than tuned.
        densities = [r["density_est"] for r in runs if r.get("density_est") is not None]
        density_mean = round(sum(densities) / len(densities), 1) if densities else None
        density_spread = (round(statistics.pstdev(densities), 1)
                          if len(densities) > 1 else (0.0 if densities else None))

        if len(runs) == 1 or not midpoints:
            primary["repeat_count"] = len(runs)
            primary["estimate_pct"] = midpoints[0] if midpoints else None
            primary["estimate_spread"] = 0.0 if midpoints else None
            primary["repeat_bands"] = [r.get("band") for r in runs]
            primary["density_est"] = density_mean
            primary["density_spread"] = density_spread
            return primary

        mean = sum(midpoints) / len(midpoints)
        spread = statistics.pstdev(midpoints) if len(midpoints) > 1 else 0.0

        result = dict(primary)
        result["band"] = band_for_pct(
            mean, VARIANT_BAND_SCALE.get(variant_label, "standard"))
        result["estimate_pct"] = round(mean, 2)
        result["estimate_spread"] = round(spread, 2)
        result["repeat_count"] = len(runs)
        result["repeat_bands"] = [r.get("band") for r in runs]
        result["density_est"] = density_mean
        result["density_spread"] = density_spread
        # Wall clock, not the sum: the rotations run concurrently.
        result["inference_time_ms"] = max(r.get("inference_time_ms", 0) for r in runs)
        result["justification"] = (
            f"[{len(runs)} rotations : {', '.join(str(r.get('band')) for r in runs)}"
            f" -> {mean:.1f}%] " + (primary.get("justification") or "")
        )
        return result

    # Each variant is an independent, blocking Azure call (chat.completions.create
    # isn't awaitable, same as elsewhere in this file) -- run them concurrently
    # on the default executor rather than one after another, so comparing N
    # variants on one photo costs roughly one call's worth of wall-clock time,
    # not N.
    loop = asyncio.get_running_loop()
    jobs = [(v, a, m) for v in requested_variants for a, m in transforms]
    all_runs = list(await asyncio.gather(*[
        loop.run_in_executor(None, call_variant, v, a, m) for v, a, m in jobs
    ]))

    # Back to one result per variant: the rotations are the same question
    # asked several ways, not separate findings.
    by_variant = {v: [] for v in requested_variants}
    for (variant_label, _), run in zip(jobs, all_runs):
        by_variant[variant_label].append(run)
    parsed_results = [aggregate(v, by_variant[v]) for v in requested_variants]

    # Lot/real-value lookup happens ONCE per photo, not once per variant --
    # every variant is being tested against the exact same physical sample,
    # so there's exactly one lot/real-value answer to attach to all of them.
    # Best-effort: a lookup failure here should never hide the actual
    # analysis results the operator is waiting on.
    lot_id = None
    lot_text = lot_number.strip() or None
    real_pct_value = None
    real_pct_source = None
    lookup_error = None
    try:
        if is_training and not lot_text:
            # Auto-ID rather than require a human to invent and track one
            # by hand -- self-organizing by the FIRST requested variant's
            # predicted band. That's now just a grouping label, not a
            # precise description: with multiple variants, the same
            # physical sample can land in different bands per variant --
            # prompt_version on each row is the real way to tell them apart.
            slug = band_slug(parsed_results[0].get("band"))
            count_resp = (
                supabase.table("vision_band_estimates")
                .select("id", count="exact")
                .eq("is_training", True)
                .execute()
            )
            seq = (count_resp.count or 0) + 1
            lot_text = f"CALIB-{slug}-{seq:04d}"
        elif not is_training and lot_text:
            lot_resp = supabase.table("lots").select("id").ilike("lot_number", lot_text).limit(1).execute()
            if lot_resp.data:
                lot_id = lot_resp.data[0]["id"]

        if lot_id:
            # Real lot matched -- pull the authoritative lab values
            # directly, rather than trust a manually re-typed duplicate
            # of something already recorded elsewhere. If the lab result
            # simply hasn't landed yet, this correctly stays null --
            # exactly the "run this only once results exist" workflow.
            #
            # ME% is computed here, not looked up -- confirmed directly
            # against real data that it's never stored as its own row,
            # only its two components (me_organic_wt, bulk_density) are.
            component_ids = get_me_pct_component_ids()
            if component_ids and component_ids[0] and component_ids[1]:
                wt_id, density_id = component_ids
                comp_resp = (
                    supabase.table("test_results")
                    .select("result_value, test_id")
                    .eq("lot_id", lot_id)
                    .in_("test_id", [wt_id, density_id])
                    .eq("is_superseded", False)
                    .execute()
                )
                values = {row["test_id"]: row["result_value"] for row in (comp_resp.data or [])}
                wt = values.get(wt_id)
                density = values.get(density_id)
                if wt is not None and density is not None and density > 0:
                    real_pct_value = round((wt / density) * 100, 2)
                    real_pct_source = "lot_lookup"
                else:
                    real_pct_source = "lot_matched_no_result_yet"
        elif real_pct.strip():
            # Training/reference modes, or a real-mode lot number that
            # didn't match -- these have no lot to look anything up
            # from, so a manually-entered known value is genuinely
            # necessary here, not a workaround.
            try:
                real_pct_value = float(real_pct.strip())
                real_pct_source = "manual"
            except ValueError:
                pass
    except Exception as e:
        lookup_error = f"{type(e).__name__}: {e}"
        print(f"[vision_band_estimates lot/real-pct lookup failed] {lookup_error}")

    # Recording is per-variant and best-effort -- one variant's insert
    # failing shouldn't hide the others' results.
    for parsed in parsed_results:
        parsed["recorded_as"] = lot_text
        parsed["real_pct_used"] = real_pct_value
        parsed["real_pct_source"] = real_pct_source
        parsed["error_flagged"] = is_outlier(parsed.get("band"), real_pct_value)
        parsed["storage_path"] = capture_storage_path
        if lookup_error:
            parsed["recording_error"] = lookup_error
            continue
        try:
            insert_resp = supabase.table("vision_band_estimates").insert({
                "lot_id": lot_id,
                "lot_number_text": lot_text,
                "predicted_band": parsed.get("band"),
                "confidence": parsed.get("confidence"),
                "factors": parsed.get("factors"),
                "justification": parsed.get("justification"),
                "raw_response": parsed.get("raw"),
                "real_me_pct": real_pct_value,
                "storage_path": capture_storage_path,
                "error_flagged": parsed["error_flagged"],
                "model": parsed["model"],
                "inference_time_ms": parsed["inference_time_ms"],
                "created_by": operator["id"],
                "is_training": is_training,
                "prompt_version": parsed["prompt_version"],
                "prompt_hash": parsed["prompt_hash"],
                "reference_count": parsed["reference_count"],
                "repeat_count": parsed.get("repeat_count", 1),
                "estimate_pct": parsed.get("estimate_pct"),
                "estimate_spread": parsed.get("estimate_spread"),
                "density_est": parsed.get("density_est"),
                "density_spread": parsed.get("density_spread"),
            }).execute()

            # Defensive: some client/API combinations can return a response
            # with no error raised but also no actual row -- treat "insert
            # claimed success but nothing came back" as a real failure to
            # surface, rather than silently report success when the table
            # stayed untouched.
            if not insert_resp.data:
                raise RuntimeError(f"Insert returned no data -- response: {insert_resp!r}")

            parsed["recorded_id"] = insert_resp.data[0].get("id")
        except Exception as e:
            parsed["recording_error"] = f"{type(e).__name__}: {e}"
            print(f"[vision_band_estimates recording failed] {type(e).__name__}: {e}")

    return {
        "results": parsed_results,
        "recorded_as": lot_text,
        "real_pct_used": real_pct_value,
        "real_pct_source": real_pct_source,
    }


@app.post("/reference-capture")
async def reference_capture(
    file: UploadFile = File(...),
    category: str = Form("meo_density"),
    real_pct: str = Form(""),
    description: str = Form(""),
    operator: dict = Depends(require_role("qc")),
):
    """Saves a new reference photo directly to Supabase Storage plus its
    metadata row -- no Azure call involved, this purely records a known
    example for future band-test calls to reference. Verifies both
    writes actually succeeded rather than trusting the absence of an
    exception alone, same lesson learned the hard way with
    vision_band_estimates -- a request completing without an error isn't
    proof a row or file genuinely landed."""
    contents = await file.read()
    ext = os.path.splitext(file.filename or "")[1] or ".jpg"
    storage_path = f"{category}/{uuid.uuid4().hex}{ext}"

    try:
        upload_resp = supabase.storage.from_(REFERENCE_BUCKET).upload(
            storage_path, contents, file_options={"content-type": file.content_type or "image/jpeg"}
        )
        if not upload_resp:
            raise RuntimeError(f"Storage upload returned no response: {upload_resp!r}")

        real_pct_value = None
        if real_pct.strip():
            try:
                real_pct_value = float(real_pct.strip())
            except ValueError:
                pass

        insert_resp = supabase.table("vision_reference_images").insert({
            "category": category,
            "storage_path": storage_path,
            "real_pct": real_pct_value,
            "description": description.strip() or None,
            "created_by": operator["id"],
        }).execute()

        if not insert_resp.data:
            raise RuntimeError(f"Insert returned no data -- response: {insert_resp!r}")

        clear_reference_cache()
        return {"status": "ok", "storage_path": storage_path, "id": insert_resp.data[0].get("id")}

    except Exception as e:
        print(f"[reference_capture failed] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Échec de l'enregistrement : {type(e).__name__}: {e}")


# --- Bench rig capture queue -------------------------------------------------
#
# Three endpoints, all device-authenticated, that let the Pi ask for work and
# report back. The browser never talks to the Pi and the Pi never talks to
# Supabase; each side reaches the one place both can reach.
#
# Rig images go in the band-test bucket under a rig/ prefix rather than a
# bucket of their own, purely so there is nothing extra to create by hand
# before this works. It does mean an analysed rig photo is stored twice --
# once here as the untouched original, once by azure_band_test when the
# browser submits it. That is a few hundred kilobytes and the original being
# preserved separately is arguably worth having.


@app.post("/band-estimates/real-pct")
def band_estimates_set_real_pct(
    ids: str = Form(...),
    real_pct: str = Form(...),
    operator: dict = Depends(require_role("qc")),
):
    """Record the measured ME% against estimates already taken.

    Deliberately after the fact. Entering the known value before the photo
    lets it colour everything downstream -- how the sample gets spread, which
    frame looks representative enough to keep, whether a surprising band gets
    re-shot "because something went wrong". None of that is dishonesty; it is
    ordinary anchoring, and it quietly turns a calibration set into a record
    of what the operator expected. Photograph, estimate, then measure.

    Takes several ids because one capture produces one row per prompt variant
    compared. They are the same physical sample, so they share the same
    measured value.

    error_flagged is recomputed per row rather than copied: each variant
    predicted its own band, so each has its own disagreement with the truth.
    """
    id_list = [i.strip() for i in ids.split(",") if i.strip()]
    if not id_list:
        raise HTTPException(status_code=400, detail="Aucun identifiant fourni.")
    try:
        value = float(real_pct.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Valeur invalide : {real_pct!r}")

    updated = 0
    try:
        for estimate_id in id_list:
            row_resp = (
                supabase.table("vision_band_estimates")
                .select("predicted_band")
                .eq("id", estimate_id)
                .limit(1)
                .execute()
            )
            if not row_resp.data:
                continue
            band = row_resp.data[0].get("predicted_band")
            upd = supabase.table("vision_band_estimates").update({
                "real_me_pct": value,
                "error_flagged": is_outlier(band, value),
            }).eq("id", estimate_id).execute()
            updated += len(upd.data or [])

        return {"status": "ok", "updated": updated, "real_pct": value}
    except Exception as e:
        print(f"[band_estimates_set_real_pct failed] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Échec : {type(e).__name__}: {e}")


@app.post("/band-estimates/backfill")
def band_estimates_backfill(
    lot_number: str = Form(...),
    operator: dict = Depends(require_role("qc")),
):
    """Attach the lab ME% to estimates recorded before it existed.

    The bench workflow runs photo-first: separating and weighing the MEO
    destroys the levelled dish, so the picture has to be taken before the
    number can be known. Every rig estimate is therefore recorded with a null
    real_pct and stays that way until the operator saves their results.

    Call this after saving. It recomputes ME% the same way azure_band_test
    does -- from the two stored components, since ME% is never a row of its
    own -- and fills in any estimate for this lot still missing it.

    Deliberately server-side rather than letting the browser write the number
    it happens to have on screen: the value that gets recorded should be the
    one actually saved to test_results, not a parallel copy that could differ
    from it. Same reasoning as the lookup in azure_band_test.
    """
    lot_text = lot_number.strip()
    if not lot_text:
        raise HTTPException(status_code=400, detail="Numéro de lot requis.")

    try:
        lot_resp = supabase.table("lots").select("id").ilike("lot_number", lot_text).limit(1).execute()
        if not lot_resp.data:
            return {"status": "ok", "updated": 0, "reason": "lot_not_found"}
        lot_id = lot_resp.data[0]["id"]

        component_ids = get_me_pct_component_ids()
        if not component_ids or not component_ids[0] or not component_ids[1]:
            return {"status": "ok", "updated": 0, "reason": "component_ids_unavailable"}

        wt_id, density_id = component_ids
        comp_resp = (
            supabase.table("test_results")
            .select("result_value, test_id")
            .eq("lot_id", lot_id)
            .in_("test_id", [wt_id, density_id])
            .eq("is_superseded", False)
            .execute()
        )
        values = {row["test_id"]: row["result_value"] for row in (comp_resp.data or [])}
        wt, density = values.get(wt_id), values.get(density_id)
        if wt is None or density is None or not density > 0:
            return {"status": "ok", "updated": 0, "reason": "no_result_yet"}

        real_pct_value = round((wt / density) * 100, 2)

        # Only rows still missing the value. An estimate that already has one
        # is never rewritten -- if the lab result is later corrected, that is
        # a decision for a human, not a silent side effect of pressing save.
        update_resp = (
            supabase.table("vision_band_estimates")
            .update({"real_pct": real_pct_value, "real_pct_source": "lot_lookup"})
            .eq("lot_id", lot_id)
            .is_("real_pct", "null")
            .execute()
        )
        return {
            "status": "ok",
            "updated": len(update_resp.data or []),
            "real_pct": real_pct_value,
        }

    except Exception as e:
        print(f"[band_estimates_backfill failed] {lot_text}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Échec du rattrapage : {type(e).__name__}")


@app.get("/capture-commands/next")
def capture_commands_next(_: bool = Depends(verify_rig_key)):
    """Claim the oldest pending capture command, or report there is none.

    Returns {"command": null} rather than 404 when the queue is empty: the
    poller hits this every few seconds forever, and an empty queue is the
    normal case, not an error. Logging it as one would bury any real failure
    in noise."""
    try:
        resp = supabase.rpc("claim_capture_command", {}).execute()
    except Exception as e:
        print(f"[capture_commands_next failed] {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Échec de la file : {type(e).__name__}")

    data = resp.data
    if isinstance(data, list):
        data = data[0] if data else None
    if not data or not data.get("id"):
        return {"command": None}

    return {
        "command": {
            "id": data["id"],
            "lot_id": data.get("lot_id"),
            "lot_number": data.get("lot_number"),
            # Older rows predate the column and are all plain captures, so an
            # absent kind means capture rather than an error.
            "kind": data.get("kind") or "capture",
        }
    }


@app.post("/capture-commands/{command_id}/complete")
async def capture_commands_complete(
    command_id: str,
    file: UploadFile = File(None),
    ir_file: UploadFile = File(None),
    result: str = Form(None),
    _: bool = Depends(verify_rig_key),
):
    """Store whatever the command produced and mark it done.

    Two shapes of result arrive here. A capture or a preview sends images. A
    calibration stage sends measurements and no image at all -- it changed the
    rig's settings rather than photographing anything. Both are complete
    results, so both finish through the same endpoint rather than through a
    second one that would duplicate the storage-then-row ordering below.

    The IR frame is optional -- the rig runs perfectly well with the IR
    boards unattached, and a visible-only capture is a complete result rather
    than a degraded one.

    Storage first, row second, and the row only if storage succeeded. The
    reverse order would leave commands marked done that point at images which
    do not exist, and the operator would be told their photo was ready."""

    async def _store(upload, band):
        contents = await upload.read()
        ext = os.path.splitext(upload.filename or "")[1] or ".jpg"
        path = f"rig/{command_id}_{band}{ext}"
        resp = supabase.storage.from_(BAND_TEST_CAPTURE_BUCKET).upload(
            path,
            contents,
            file_options={"content-type": upload.content_type or "image/jpeg"},
        )
        if not resp:
            raise RuntimeError(f"Storage upload returned no response: {resp!r}")
        return path

    try:
        if file is None:
            if result is None:
                raise HTTPException(
                    status_code=400,
                    detail="Une commande terminée doit fournir une image ou un résultat.",
                )
            try:
                parsed = json.loads(result)
            except json.JSONDecodeError as e:
                raise HTTPException(
                    status_code=400, detail=f"Résultat JSON invalide : {e}"
                )
            update_resp = supabase.table("capture_commands").update({
                "status": "done",
                "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "result": parsed,
            }).eq("id", command_id).execute()
            if not update_resp.data:
                raise RuntimeError(f"Update matched no rows -- response: {update_resp!r}")
            return {"status": "ok", "result": parsed}

        image_path = await _store(file, "visible")
        ir_path = await _store(ir_file, "ir") if ir_file is not None else None

        update_resp = supabase.table("capture_commands").update({
            "status": "done",
            "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "image_path": image_path,
            "ir_image_path": ir_path,
        }).eq("id", command_id).execute()

        if not update_resp.data:
            raise RuntimeError(f"Update matched no rows -- response: {update_resp!r}")

        return {"status": "ok", "image_path": image_path, "ir_image_path": ir_path}

    except Exception as e:
        print(f"[capture_commands_complete failed] {command_id}: {type(e).__name__}: {e}")
        raise HTTPException(status_code=500, detail=f"Échec de l'envoi : {type(e).__name__}: {e}")


@app.post("/capture-commands/{command_id}/fail")
def capture_commands_fail(
    command_id: str,
    error: str = Form(""),
    _: bool = Depends(verify_rig_key),
):
    """Record that a capture failed, so the operator is told rather than left
    watching a spinner until the stale-claim timeout eventually fires."""
    try:
        supabase.table("capture_commands").update({
            "status": "failed",
            "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "error": error[:1000] or "Erreur inconnue",
        }).eq("id", command_id).execute()
    except Exception as e:
        print(f"[capture_commands_fail failed] {command_id}: {type(e).__name__}: {e}")
    return {"status": "ok"}
