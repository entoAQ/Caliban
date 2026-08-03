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
import datetime
import hashlib
import os
import re
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


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "model_loaded": get_model() is not None,
        "started_at": STARTUP_TIME,
        "prompt_version": CALIBAN_PROMPT_VERSION,
        "prompt_hash": PROMPT_HASH,
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
BAND_PROMPT = """Tu examines une photo d'un échantillon de larves de mouche soldat noire \
(Hermetia illucens) mélangées à de la MEO (matières étrangères organiques, résidu d'élevage \
aussi appelé frass), pour estimer le niveau de contamination visible.

Important : mesure la densité par rapport à la surface couverte par l'échantillon lui-même \
(larves + MEO), PAS par rapport à l'ensemble de la photo. Le plateau contient souvent de \
l'espace vide autour de l'échantillon pour permettre un étalement en une seule couche -- cet \
espace vide ne doit jamais être compté comme faisant partie d'un échantillon "propre".

Important également : la MEO empile et se chevauche facilement, même dans un échantillon bien \
étalé -- une partie de la matière reste cachée sous la surface visible. Si tu observes des \
signes d'empilement, de relief ou de superposition plutôt qu'une couche unique et bien étalée, \
considère que la quantité réelle de MEO est probablement plus élevée que ce que la surface \
visible seule suggère, et penche vers une bande supérieure en conséquence.

Ne tente PAS de compter les particules individuelles -- donne une impression visuelle globale \
de densité, comme le ferait une personne qui regarde rapidement le plateau.

Réponds EXACTEMENT selon ce format, rien d'autre avant ou après :

BANDE: [une seule valeur parmi : <1%, 1-3%, 3-7%, 7-10%, 10-14%, >14%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
JUSTIFICATION: [une phrase, ce qui a motivé ce choix]"""

# Manually bumped -- update this any time BAND_PROMPT changes meaningfully
# (wording, band boundaries, new instructions). Human-readable label for
# discussing results ("v1.2 was clearly better at the high end").
CALIBAN_PROMPT_VERSION = "1.1"

# Computed automatically from the actual prompt text every time this
# module loads -- guaranteed accurate even if CALIBAN_PROMPT_VERSION
# above is forgotten. This is the real, tamper-proof way to know
# whether two results actually came from the same prompt.
PROMPT_HASH = hashlib.md5(BAND_PROMPT.encode("utf-8")).hexdigest()[:8]


def parse_band_response(text):
    """Extracts the structured band/confidence fields from BAND_PROMPT's
    response -- falls back to returning the raw text untouched if the
    model didn't follow the exact format, rather than silently dropping
    a real answer that just wasn't formatted as expected."""
    band = confidence = justification = None
    for line in text.strip().splitlines():
        if line.upper().startswith("BANDE:"):
            band = line.split(":", 1)[1].strip()
        elif line.upper().startswith("CONFIANCE:"):
            confidence = line.split(":", 1)[1].strip()
        elif line.upper().startswith("JUSTIFICATION:"):
            justification = line.split(":", 1)[1].strip()
    return {"band": band, "confidence": confidence, "justification": justification, "raw": text}


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

from openai import AzureOpenAI

AZURE_OPENAI_ENDPOINT = os.environ.get("AZURE_OPENAI_ENDPOINT")
AZURE_OPENAI_API_KEY = os.environ.get("AZURE_OPENAI_API_KEY")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
AZURE_OPENAI_API_VERSION = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21")

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
    operator: dict = Depends(require_role("qc")),
):
    client = get_azure_client()
    contents = await file.read()
    media_type = file.content_type or "image/jpeg"
    b64_image = base64.b64encode(contents).decode("utf-8")

    # Few-shot grounding: real reference photos with known values, judged
    # alongside the new photo rather than asked to reason about density
    # in the abstract. Falls back to prompt-only (references empty) until
    # real reference photos are provided -- see reference_images/README.
    content = [{"type": "text", "text": BAND_PROMPT}]
    references = get_reference_images("meo_density")
    if references:
        content.append({
            "type": "text",
            "text": "\n\nVoici des photos de référence avec leur pourcentage réel connu de "
                    "MEO, pour calibrer ton estimation :",
        })
        for ref in references:
            label = f"Référence -- {ref['real_pct']}% MEO réel"
            if ref.get("description"):
                label += f" ({ref['description']})"
            content.append({"type": "text", "text": label + " :"})
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{ref['b64']}"}})
        content.append({"type": "text", "text": "\n\nMaintenant, voici la photo à évaluer :"})
    content.append({"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64_image}"}})

    start = time.time()
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
    parsed["reference_count"] = len(references)

    # Recording is best-effort -- a write failure here should never hide
    # the actual analysis result the operator is waiting on.
    try:
        lot_id = None
        lot_text = lot_number.strip() or None

        if is_training and not lot_text:
            # Auto-ID rather than require a human to invent and track one
            # by hand -- self-organizing by predicted band, ready to
            # extend with a detected material type once that exists
            # (e.g. plastic detection), not just contamination level.
            slug = band_slug(parsed.get("band"))
            count_resp = (
                supabase.table("vision_band_estimates")
                .select("id", count="exact")
                .eq("is_training", True)
                .eq("predicted_band", parsed.get("band"))
                .execute()
            )
            seq = (count_resp.count or 0) + 1
            lot_text = f"CALIB-{slug}-{seq:04d}"
        elif not is_training and lot_text:
            lot_resp = supabase.table("lots").select("id").ilike("lot_number", lot_text).limit(1).execute()
            if lot_resp.data:
                lot_id = lot_resp.data[0]["id"]

        real_pct_value = None
        real_pct_source = None

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

        insert_resp = supabase.table("vision_band_estimates").insert({
            "lot_id": lot_id,
            "lot_number_text": lot_text,
            "predicted_band": parsed.get("band"),
            "confidence": parsed.get("confidence"),
            "justification": parsed.get("justification"),
            "raw_response": parsed.get("raw"),
            "real_me_pct": real_pct_value,
            "model": parsed["model"],
            "inference_time_ms": elapsed_ms,
            "created_by": operator["id"],
            "is_training": is_training,
            "prompt_version": CALIBAN_PROMPT_VERSION,
            "prompt_hash": PROMPT_HASH,
            "reference_count": len(references),
        }).execute()

        # Defensive: some client/API combinations can return a response
        # with no error raised but also no actual row -- treat "insert
        # claimed success but nothing came back" as a real failure to
        # surface, rather than silently report success when the table
        # stayed untouched.
        if not insert_resp.data:
            raise RuntimeError(f"Insert returned no data -- response: {insert_resp!r}")

        parsed["recorded_as"] = lot_text
        parsed["real_pct_used"] = real_pct_value
        parsed["real_pct_source"] = real_pct_source
        parsed["recorded_id"] = insert_resp.data[0].get("id")
    except Exception as e:
        parsed["recording_error"] = f"{type(e).__name__}: {e}"
        print(f"[vision_band_estimates recording failed] {type(e).__name__}: {e}")

    return parsed


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
