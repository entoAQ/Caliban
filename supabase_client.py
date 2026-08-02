"""
Supabase client + auth helper.

Uses the SERVICE ROLE key server-side (bypasses RLS, so the backend can
write on behalf of any authenticated operator). The frontend uses the
ANON/public key with supabase-js directly for login — never ship the
service role key to the browser.

Required env vars:
  SUPABASE_URL           e.g. https://xxxxx.supabase.co
  SUPABASE_SERVICE_KEY   service_role key, from Supabase project settings > API
"""
import os
from fastapi import Header, HTTPException
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

# Separate client dedicated to auth.get_user() calls specifically. Per
# Supabase's own troubleshooting docs: auth functions like get_user()
# can mutate a client's internal Authorization header, replacing its
# service-role credentials with whatever user token was just validated.
# Sharing one client between token validation and regular data queries
# (the `supabase` client above, used everywhere else) risks exactly this
# kind of state collision -- a real, documented cause of intermittent
# "invalid token" failures, not something specific to any one token.
_auth_client: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

STORAGE_BUCKET = os.environ.get("SUPABASE_BUCKET", "inspection-images")


def get_current_operator(authorization: str = Header(...)):
    """
    Verifies the Supabase JWT sent by the frontend (from supabase-js's
    session.access_token) and returns the operator's user record.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()

    try:
        user_resp = _auth_client.auth.get_user(token)
    except Exception:
        raise HTTPException(401, "Invalid or expired token")

    user = getattr(user_resp, "user", None)
    if not user:
        raise HTTPException(401, "Invalid or expired token")

    return {"id": user.id, "email": user.email}
