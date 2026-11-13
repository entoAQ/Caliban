-- Inline rig: a Teams message to Tim alone when Ariel's flash starts to fail
-- (Tim, 2026-11-13).
--
-- Why: the first TT520III died on 2026-11-13 after ~30,000 firings, its
-- misses creeping up for two days first. doctor.py's « Flash » check now
-- turns warn at 3 % of the last 200 photos missed and fail at 10 %, and fail
-- when the flash does not fire at all -- but only on the Diagnostic page,
-- where nobody looks until something else is wrong. This sends it to Tim.
--
-- HOW
--   flash_alert_state      one row per device: the last level announced.
--   post_personal_card()   the same adaptive card as post_device_card()
--                          (2026-10-27-device-watch-in-supabase.sql in
--                          sgsc-app), POSTed by pg_net to a Teams Workflows
--                          webhook that posts into Tim's own chat, not the
--                          channel. Its URL is read from Vault by NAME:
--                          teams_tim_webhook_url. No secret -> nothing sent.
--   trigger on device_status  each Ariel report (one a minute) reads the
--                          « flash » check. A message goes out only when the
--                          level RISES above the last one announced
--                          (ok -> warn, ok -> fail, warn -> fail): one message
--                          per step, not one a minute. The level drops back to
--                          ok, silently, once the check has read ok for 6 hours
--                          straight -- a miss rate hovering at 3 % flips back
--                          and forth, and 6 h keeps that to one message.
--                          « off » (the 75 s after a repair) is ignored.
--   Errors are caught: a fault here must never stop the rig's report being
--   saved, or the offline watch would announce a rig that is fine.
--
-- BEFORE RUNNING
--   1. The Teams Workflow (« Send webhook alerts to a chat », to yourself),
--      which gives a URL.
--   2. Dashboard -> Integrations -> Vault -> « Add new secret »:
--        name  = teams_tim_webhook_url
--        value = that URL
--      The form, not SQL, so the URL never sits in the SQL editor.
-- The end of this file sends ONE test message, so you see the path works.
--
-- Undo: DROP TRIGGER device_status_flash_alert ON public.device_status;

BEGIN;

CREATE TABLE IF NOT EXISTS public.flash_alert_state (
  device     text PRIMARY KEY,
  level      text NOT NULL DEFAULT 'ok' CHECK (level IN ('ok', 'warn', 'fail')),
  ok_since   timestamptz,          -- since when the check has read ok; null while warn/fail
  alerted_at timestamptz
);
-- Only the trigger (security definer) touches it: RLS on, no policies.
ALTER TABLE public.flash_alert_state ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.flash_alert_state FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.post_personal_card(p_title text, p_text text, p_good boolean)
RETURNS void
LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO 'public'
AS $$
declare
  v_url text;
begin
  select decrypted_secret into v_url from vault.decrypted_secrets where name = 'teams_tim_webhook_url';
  if coalesce(v_url, '') = '' then
    return;
  end if;
  perform net.http_post(
    url     := v_url,
    headers := '{"Content-Type": "application/json"}'::jsonb,
    body    := jsonb_build_object(
      'type', 'message',
      'attachments', jsonb_build_array(jsonb_build_object(
        'contentType', 'application/vnd.microsoft.card.adaptive',
        'content', jsonb_build_object(
          '$schema', 'http://adaptivecards.io/schemas/adaptive-card.json',
          'type', 'AdaptiveCard', 'version', '1.4',
          'body', jsonb_build_array(
            jsonb_build_object('type', 'TextBlock', 'size', 'Medium', 'weight', 'Bolder', 'wrap', true,
                               'color', case when p_good then 'Good' else 'Attention' end, 'text', p_title),
            jsonb_build_object('type', 'TextBlock', 'wrap', true, 'text', p_text)))))),
    timeout_milliseconds := 15000);
end;
$$;

CREATE OR REPLACE FUNCTION public.trg_flash_alert()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO 'public'
AS $$
declare
  v_check jsonb;
  v_st    text;
  v_prev  public.flash_alert_state;
  v_rank  constant jsonb := '{"ok": 0, "warn": 1, "fail": 2}';
begin
  begin
    select c into v_check
      from jsonb_array_elements(coalesce(NEW.report -> 'checks', '[]'::jsonb)) c
     where c ->> 'id' = 'flash'
     limit 1;
    v_st := v_check ->> 'status';
    if v_st is null or not (v_rank ? v_st) then
      return null;                    -- no flash check, or « off » just after a repair
    end if;

    insert into flash_alert_state (device, level, ok_since) values (NEW.device, 'ok', now())
    on conflict (device) do nothing;
    select * into v_prev from flash_alert_state where device = NEW.device for update;

    if v_st = 'ok' then
      if v_prev.ok_since is null then
        update flash_alert_state set ok_since = now() where device = NEW.device;
      elsif v_prev.level <> 'ok' and v_prev.ok_since < now() - interval '6 hours' then
        update flash_alert_state set level = 'ok' where device = NEW.device;
      end if;
      return null;
    end if;

    update flash_alert_state set ok_since = null where device = NEW.device;
    if (v_rank ->> v_st)::int > (v_rank ->> v_prev.level)::int then
      update flash_alert_state set level = v_st, alerted_at = now() where device = NEW.device;
      perform post_personal_card(
        case v_st
          when 'warn' then '⚠ Flash d''Ariel : il commence à rater'
          else             '⛔ Flash d''Ariel : à remplacer'
        end,
        coalesce(v_check ->> 'detail', '')
          || case when v_st = 'warn'
               then ' Préparer le flash de rechange : le premier est mort deux jours après ce stade.'
               else '' end
          || coalesce(' ' || (v_check ->> 'manual'), '')
          || ' SGSC → Diagnostic des bancs pour le détail.',
        false);
    end if;
  exception when others then
    raise warning 'flash alert: %', sqlerrm;
  end;
  return null;
end;
$$;

REVOKE EXECUTE ON FUNCTION public.post_personal_card(text, text, boolean) FROM PUBLIC, anon, authenticated, service_role;
REVOKE EXECUTE ON FUNCTION public.trg_flash_alert() FROM PUBLIC, anon, authenticated, service_role;

DROP TRIGGER IF EXISTS device_status_flash_alert ON public.device_status;
CREATE TRIGGER device_status_flash_alert
  AFTER INSERT OR UPDATE ON public.device_status
  FOR EACH ROW WHEN (NEW.device = 'ariel')
  EXECUTE FUNCTION public.trg_flash_alert();

COMMIT;

-- One test message. If it does not arrive within a minute, see what Teams
-- answered (202 = accepted; 401/403 = the Workflow does not allow « Anyone »;
-- no row = the Vault secret is missing or misnamed):
--   select status_code, left(content, 300) from net._http_response order by created desc limit 3;
SELECT public.post_personal_card('✓ Test : alertes du flash d''Ariel',
  'Si vous lisez ceci, les alertes du flash arrivent bien ici, et seulement ici.', true);
