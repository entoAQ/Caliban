-- Inline rig: operator confirmations from the destoner screen.
--
-- Run once in the Supabase SQL editor, BEFORE deploying the Caliban that
-- has /inline/acks. Inline readings themselves need nothing new: they go into
-- vision_band_estimates with source = 'inline' and the Pi's reading id
-- (IL-YYYYMMDD-HHMMSS) as lot_number_text.
--
-- A confirmation is what the operator says they did about a reading:
--   adjusted       "Réglage fait" -- after AUGMENTER, or DIMINUER when the
--                  rejects held larvae
--   clean_rejects  "Rejets propres -- aucun réglage" after DIMINUER
--   set            "Réglé à X" -- the destoner set to the value the screen
--                  asked for, by its own gauge; setting/unit hold that value
--
-- Kept beside the readings rather than on them so a reading can be answered
-- more than once (a mis-tap corrected) without losing what was said first,
-- and so readings and confirmations can be laid side by side later to learn
-- which settings actually move the next reading -- the only way "Régler à X"
-- will ever have a value of X worth showing.

create table if not exists inline_acks (
    id          uuid primary key default gen_random_uuid(),
    reading_id  text not null,
    choice      text not null check (choice in ('adjusted', 'clean_rejects', 'set')),
    acked_at    timestamptz not null,     -- when the operator tapped, by the Pi's clock
    setting     numeric,                  -- 'set' only
    unit        text,
    received_at timestamptz not null default now(),
    -- The Pi resends anything it is not sure arrived; this makes that harmless.
    unique (reading_id, acked_at)
);

create index if not exists inline_acks_reading_idx on inline_acks (reading_id);

-- Written only by Caliban (service role, bypasses RLS). Readable by QC and up,
-- like vision_band_estimates.
alter table inline_acks enable row level security;
drop policy if exists qc_select_inline_acks on inline_acks;
create policy qc_select_inline_acks on inline_acks
    for select to authenticated using (public.role_gte('qc'));

create index if not exists vision_band_estimates_inline_idx
    on vision_band_estimates (created_at desc)
    where source = 'inline';


-- Optional: inline rig settings (INLINE_DEFAULTS in app/main.py). Any key left
-- out keeps its default. mode stays 'shadow' until the inline readings have
-- been compared with tray samples and lab results.
--
--   insert into system_config (key, value)
--   values ('inline_settings', '{"mode": "shadow", "interval_min": 5}')
--   on conflict (key) do update set value = excluded.value;


-- Recent inline readings with what the operator said about each:
--
--   select to_char(e.created_at at time zone 'America/Toronto', 'YYYY-MM-DD HH24:MI') as heure,
--          e.lot_number_text as lecture, e.predicted_band, e.estimate_pct,
--          e.operator_instruction, e.instruction_alert,
--          a.choice, a.setting, a.unit,
--          to_char(a.acked_at at time zone 'America/Toronto', 'HH24:MI') as confirme
--   from vision_band_estimates e
--   left join inline_acks a on a.reading_id = e.lot_number_text
--   where e.source = 'inline' and e.operator_instruction is not null
--   order by e.created_at desc
--   limit 50;
