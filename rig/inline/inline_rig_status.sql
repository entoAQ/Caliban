-- Inline rig: the destoner screen's current state, mirrored for SGSC.
--
-- Run once in the Supabase SQL editor, BEFORE deploying the Caliban that has
-- /inline/state. The Pi posts what its touchscreen is showing -- latest
-- reading, recent history, line running or not, health, the operator's last
-- confirmation -- whenever it changes and at least once a minute; Caliban
-- keeps the latest copy here, one row per rig. SGSC's destoner-mirror.html
-- reads it to show the same screen, read-only, anywhere.
--
-- Only the latest state is kept: the history of readings is already in
-- vision_band_estimates (source = 'inline') and the confirmations in
-- inline_acks. updated_at says how fresh it is -- a mirror minutes old means
-- the rig has stopped reporting.

create table if not exists inline_rig_status (
    device     text primary key,
    state      jsonb not null,
    updated_at timestamptz not null default now()
);

-- Written only by Caliban (service role). Readable by QC and up.
alter table inline_rig_status enable row level security;
drop policy if exists qc_select_inline_rig_status on inline_rig_status;
create policy qc_select_inline_rig_status on inline_rig_status
    for select to authenticated using (public.role_gte('qc'));
