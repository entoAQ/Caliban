"""Shared paths and HTTP helpers for the Label Studio scripts in this folder.

Stdlib only on purpose: these run from whatever Python is on the labeling PC,
not from the Caliban app's environment.

Everything Label Studio owns lives under one folder outside the repo,
INLINE_LABELING_DIR (default ~/inline_labeling):

    images/        the inline photos, pulled from Supabase by pull_inline.py
    ls-data/       Label Studio's own database and settings
    ls-login.txt   the local Label Studio login and API token, made by start.ps1
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(os.environ.get("INLINE_LABELING_DIR", Path.home() / "inline_labeling"))
IMAGES = ROOT / "images"
LOGIN_FILE = ROOT / "ls-login.txt"

LS_URL = os.environ.get("LABEL_STUDIO_URL", "http://127.0.0.1:8080")
PROJECT_TITLE = "Chute en ligne - Larves / Frass"


def http(method, url, headers=None, body=None, raw=False):
    data = None
    headers = dict(headers or {})
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=120) as resp:
        content = resp.read()
    return content if raw else (json.loads(content) if content else None)


def ls_token():
    """The API token start.ps1 handed Label Studio on its first start."""
    if not LOGIN_FILE.exists():
        raise SystemExit(f"{LOGIN_FILE} not found: run start.ps1 once first.")
    for line in LOGIN_FILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("token="):
            return line.split("=", 1)[1].strip()
    raise SystemExit(f"No token= line in {LOGIN_FILE}.")


def ls(method, path, body=None, **params):
    url = LS_URL + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        return http(method, url, {"Authorization": f"Token {ls_token()}"}, body)
    except urllib.error.HTTPError as e:
        # Label Studio says what was wrong in the body; urllib drops it.
        raise SystemExit(f"Label Studio {method} {path}: {e.code} {e.read().decode(errors='replace')}")
    except urllib.error.URLError as e:
        raise SystemExit(f"Label Studio not reachable at {LS_URL} ({e.reason}): is start.ps1 running?")


def find_project():
    """The inline project's id, or None. Matched by title so the scripts need
    no id written down anywhere."""
    resp = ls("GET", "/api/projects", page_size=100)
    for p in resp.get("results", resp) if isinstance(resp, dict) else resp:
        if p["title"] == PROJECT_TITLE:
            return p["id"]
    return None
