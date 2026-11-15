-- Inline rig: a Teams message to Tim alone when Ariel's camera turns or goes
-- blurry (Tim, 2026-11-15).
--
-- Why: the lens worked loose on 2026-11-10 and the photos blurred for three
-- days before anyone noticed (crispness 0.33 -> 0.18), and the camera has
-- turned or shifted three times in a month. doctor.py's « Mise au point »
-- (id focus) and « Orientation caméra » (id view) checks now see both, but
-- like the flash check only on the Diagnostic page. This sends them to Tim,
-- the same way flash_alert.sql does for the flash.
--
-- HOW
--   camera_alert_state   one row per device and check: the last level
--                        announced.
--   post_personal_card() from flash_alert.sql (run 2026-11-13): Tim's own
--                        chat, URL from Vault (teams_tim_webhook_url).
--   trigger on device_status  each Ariel report (one a minute) reads the two
--                        checks. A message goes out when one RISES above the
--                        level last announced for it (both only warn today;
--                        fail is handled for later). It drops back to ok,
--                        silently, after 6 hours of ok -- the checks are
--                        medians over the last ~10 minutes of photos, but a
--                        warning that clears and returns within the hour
--                        should not be a second message.
--                        « off » (no photos yet, lip hidden by the flow, or
--                        the 75 s after a repair) is ignored.
--   Errors are caught: a fault here must never stop the rig's report being
--   saved.
--
-- Needs flash_alert.sql first (post_personal_card). No policy is added:
-- RLS on with none, only the security-definer trigger writes the table.
--
-- Undo: DROP TRIGGER device_status_camera_alert ON public.device_status;

BEGIN;

CREATE TABLE IF NOT EXISTS public.camera_alert_state (
  device     text NOT NULL,
  check_id   text NOT NULL,
  level      text NOT NULL DEFAULT 'ok' CHECK (level IN ('ok', 'warn', 'fail')),
  ok_since   timestamptz,          -- since when the check has read ok; null while warn/fail
  alerted_at timestamptz,
  PRIMARY KEY (device, check_id)
);
ALTER TABLE public.camera_alert_state ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.camera_alert_state FROM PUBLIC, anon, authenticated;

CREATE OR REPLACE FUNCTION public.trg_camera_alert()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path TO 'public'
AS $$
declare
  v_check jsonb;
  v_id    text;
  v_st    text;
  v_prev  public.camera_alert_state;
  v_rank  constant jsonb := '{"ok": 0, "warn": 1, "fail": 2}';
begin
  for v_check in
    select c from jsonb_array_elements(coalesce(NEW.report -> 'checks', '[]'::jsonb)) c
     where c ->> 'id' in ('focus', 'view')
  loop
    begin
      v_id := v_check ->> 'id';
      v_st := v_check ->> 'status';
      if v_st is null or not (v_rank ? v_st) then
        continue;                     -- « off »: nothing to conclude
      end if;

      insert into camera_alert_state (device, check_id, level, ok_since)
      values (NEW.device, v_id, 'ok', now())
      on conflict (device, check_id) do nothing;
      select * into v_prev from camera_alert_state
       where device = NEW.device and check_id = v_id for update;

      if v_st = 'ok' then
        if v_prev.ok_since is null then
          update camera_alert_state set ok_since = now() where device = NEW.device and check_id = v_id;
        elsif v_prev.level <> 'ok' and v_prev.ok_since < now() - interval '6 hours' then
          update camera_alert_state set level = 'ok' where device = NEW.device and check_id = v_id;
        end if;
        continue;
      end if;

      update camera_alert_state set ok_since = null where device = NEW.device and check_id = v_id;
      if (v_rank ->> v_st)::int > (v_rank ->> v_prev.level)::int then
        update camera_alert_state set level = v_st, alerted_at = now()
         where device = NEW.device and check_id = v_id;
        perform post_personal_card(
          case v_id
            when 'focus' then '⚠ Caméra d''Ariel : image floue'
            else              '⚠ Caméra d''Ariel : elle a tourné ou bougé'
          end,
          coalesce(v_check ->> 'detail', '')
            || ' Les lectures depuis ce moment sont à prendre avec prudence.'
            || coalesce(' ' || (v_check ->> 'manual'), '')
            || ' SGSC → Diagnostic des bancs pour le détail.',
          false);
      end if;
    exception when others then
      raise warning 'camera alert (%): %', v_id, sqlerrm;
    end;
  end loop;
  return null;
end;
$$;

REVOKE EXECUTE ON FUNCTION public.trg_camera_alert() FROM PUBLIC, anon, authenticated, service_role;

DROP TRIGGER IF EXISTS device_status_camera_alert ON public.device_status;
CREATE TRIGGER device_status_camera_alert
  AFTER INSERT OR UPDATE ON public.device_status
  FOR EACH ROW WHEN (NEW.device = 'ariel')
  EXECUTE FUNCTION public.trg_camera_alert();

COMMIT;

-- One test message, so you see it arrives:
SELECT public.post_personal_card('✓ Test : alertes caméra d''Ariel',
  'Si vous lisez ceci, les alertes « image floue » et « caméra tournée » arrivent bien ici.', true);
