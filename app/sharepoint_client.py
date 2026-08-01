"""
SharePoint access via Microsoft Graph API, using app-only (client
credentials) authentication -- no per-user Microsoft login involved
anywhere. Handles BOTH directions (upload for capture, download for the
labeling proxy) through the same credential and the same code path,
deliberately, rather than splitting SharePoint access across two
different mechanisms that could drift out of sync with each other.

Required env vars (set these on the Render service):
  MS_TENANT_ID       Azure AD tenant ID (GUID or domain like contoso.onmicrosoft.com)
  MS_CLIENT_ID       App registration's Application (client) ID
  MS_CLIENT_SECRET   App registration's client secret value
  SHAREPOINT_HOSTNAME    e.g. contoso.sharepoint.com
  SHAREPOINT_SITE_PATH   e.g. /sites/Entosystem (the site-relative path)

────────────────────────────────────────────────────────────────────────
ONE-TIME SETUP THIS CODE ASSUMES HAS ALREADY BEEN DONE (admin-level,
in Azure AD -- not something this code or I can do remotely):
────────────────────────────────────────────────────────────────────────
1. Register an app in Azure AD (portal.azure.com > App registrations > New).
2. Under API permissions, add Microsoft Graph > Application permissions >
   Sites.ReadWrite.All, then grant admin consent.
3. Under Certificates & secrets, create a new client secret -- copy its
   VALUE (not its ID) immediately, it's shown only once.
4. Set the four env vars above using those values.

Verified directly against the real, installed msal library before
writing this (ConfidentialClientApplication's constructor and
acquire_token_for_client's signature) -- the actual token exchange
against a real tenant could not be tested from this environment (no
network path to login.microsoftonline.com here), so treat the first
real call as the actual first test of this specific piece.
"""
import os
import time
import requests
import msal

MS_TENANT_ID = os.environ.get("MS_TENANT_ID")
MS_CLIENT_ID = os.environ.get("MS_CLIENT_ID")
MS_CLIENT_SECRET = os.environ.get("MS_CLIENT_SECRET")
SHAREPOINT_HOSTNAME = os.environ.get("SHAREPOINT_HOSTNAME")
SHAREPOINT_SITE_PATH = os.environ.get("SHAREPOINT_SITE_PATH")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_msal_app = None
_site_id_cache = None


def _get_msal_app():
    global _msal_app
    if _msal_app is None:
        for name, val in [("MS_TENANT_ID", MS_TENANT_ID), ("MS_CLIENT_ID", MS_CLIENT_ID), ("MS_CLIENT_SECRET", MS_CLIENT_SECRET)]:
            if not val:
                raise RuntimeError(f"{name} n'est pas configurée sur ce service.")
        _msal_app = msal.ConfidentialClientApplication(
            MS_CLIENT_ID,
            client_credential=MS_CLIENT_SECRET,
            authority=f"https://login.microsoftonline.com/{MS_TENANT_ID}",
        )
    return _msal_app


def _get_access_token():
    result = _get_msal_app().acquire_token_for_client(scopes=["https://graph.microsoft.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"Échec de l'authentification Microsoft Graph : {result.get('error_description', result)}")
    return result["access_token"]


def _get_site_id():
    """Resolves the human-readable hostname+path into the internal site
    ID Graph API actually needs for drive operations. Cached after the
    first successful lookup -- a site's ID never changes."""
    global _site_id_cache
    if _site_id_cache:
        return _site_id_cache
    if not SHAREPOINT_HOSTNAME or not SHAREPOINT_SITE_PATH:
        raise RuntimeError("SHAREPOINT_HOSTNAME / SHAREPOINT_SITE_PATH ne sont pas configurées sur ce service.")
    token = _get_access_token()
    resp = requests.get(
        f"{GRAPH_BASE}/sites/{SHAREPOINT_HOSTNAME}:{SHAREPOINT_SITE_PATH}",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    _site_id_cache = resp.json()["id"]
    return _site_id_cache


def upload_file(folder_path, filename, content_bytes, content_type="image/jpeg"):
    """Uploads a file to the given folder (relative to the SharePoint
    document library root, e.g. 'ScreeningPhotos' or 'TrainingPhotos').
    Returns the SharePoint-relative path to store as image_path."""
    site_id = _get_site_id()
    token = _get_access_token()
    full_path = f"{folder_path}/{filename}"
    resp = requests.put(
        f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{full_path}:/content",
        headers={"Authorization": f"Bearer {token}", "Content-Type": content_type},
        data=content_bytes,
        timeout=30,
    )
    resp.raise_for_status()
    return full_path


def download_file(sharepoint_path):
    """Fetches raw file bytes for a path previously returned by
    upload_file(). Used by the labeling proxy -- bytes pass straight
    through to the browser, never written to disk here."""
    site_id = _get_site_id()
    token = _get_access_token()
    resp = requests.get(
        f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{sharepoint_path}:/content",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.content


def list_folder(folder_path):
    """Lists files directly in a SharePoint folder -- used to discover
    training photos, which have no Supabase row to query instead (that's
    the entire point of the training toggle: Supabase never sees them)."""
    site_id = _get_site_id()
    token = _get_access_token()
    resp = requests.get(
        f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{folder_path}:/children",
        headers={"Authorization": f"Bearer {token}"},
        timeout=15,
    )
    resp.raise_for_status()
    items = resp.json().get("value", [])
    return [
        {"name": item["name"], "path": f"{folder_path}/{item['name']}"}
        for item in items
        if "file" in item  # skip subfolders, only real files
    ]
