-- Operator captures: what the operator was told, and why.
--
-- Run once in the Supabase SQL editor, BEFORE deploying the matching Caliban.
-- The new columns are only written on rig captures (a command_id was sent), so
-- a deploy that runs ahead of this script breaks operator captures only -- QC
-- uploads keep recording -- but run it first all the same.
--
-- Every operator instruction is recorded against the estimate that produced
-- it. That is the point of the whole exercise: the model has never been
-- validated at the 8% line, and the only way to learn whether the instructions
-- were right is to set each one beside the lab result that later lands for the
-- same sample.

alter table vision_band_estimates
    -- Where the photo came from: 'upload' (band-test page), 'rig' (a QC
    -- capture sent by command), 'operator' (the operator screen). Null on rows
    -- older than this column, which are all uploads or QC rig captures.
    add column if not exists source text,

    -- The queue row the photo came from. An operator sample is recorded under
    -- an OP-YYYYMMDD-HHMMSS identity with no lot; this is what lets it be traced
    -- to its capture and attached to a lot from packing time afterwards.
    add column if not exists capture_command_id uuid,

    -- What the operator was told to do to the destoner.
    add column if not exists operator_instruction text
        check (operator_instruction in ('increase', 'hold', 'decrease')),

    -- Whether AQ was flagged as well (reading at or above alert_at).
    add column if not exists instruction_alert boolean,

    -- Whether extra rotations ran because the first ones read at or above
    -- increase_at. Kept so escalated and unescalated calls can be scored
    -- against lab results separately: if escalation never changes the answer,
    -- it is cost without benefit and the default should come down.
    add column if not exists escalated boolean not null default false;

create index if not exists vision_band_estimates_operator_idx
    on vision_band_estimates (created_at desc)
    where source = 'operator';


-- Optional: override the operator defaults (OPERATOR_DEFAULTS in app/main.py).
-- Any key left out keeps its default, and a malformed value is ignored rather
-- than refused. Read fresh per capture, so a change applies on the next press.
--
--   insert into system_config (key, value)
--   values ('operator_settings',
--           '{"repeats": 2, "escalate_repeats": 6, "increase_at": 8.0, "decrease_below": 3.0, "alert_at": 13.0}')
--   on conflict (key) do update set value = excluded.value;
--
-- The prompt is deliberately not set here. Operator captures use the same
-- rig_prompt_variant AQ picks for QC captures under Gestion de l'échantillonnage.


-- Operator samples, newest first -- the ones still to be attached to a lot:
--
--   select to_char(created_at at time zone 'America/Toronto', 'YYYY-MM-DD HH24:MI') as heure,
--          lot_number_text as echantillon, lot_id, predicted_band, estimate_pct,
--          operator_instruction, instruction_alert, escalated, repeat_count
--   from vision_band_estimates
--   where source = 'operator'
--   order by created_at desc;
