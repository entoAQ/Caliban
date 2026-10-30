"""
Supabase client.

Uses the SERVICE ROLE key server-side (bypasses RLS), for the rigs' endpoints.
Never ship the service role key to a browser or a Pi.

Required env vars:
  SUPABASE_URL           e.g. https://xxxxx.supabase.co
  SUPABASE_SERVICE_KEY   service_role key, from Supabase project settings > API
"""
import os
from supabase import create_client, Client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

supabase: Client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
