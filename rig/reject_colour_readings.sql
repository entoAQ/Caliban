-- Larva share of each reject-stream photo, measured from the pixels
-- (app/reject_colour.py, no AI call), plus the weighed samples that
-- calibrate it.
--
-- Run once in the Supabase SQL editor, BEFORE deploying the Caliban change
-- that writes here (an insert into a missing table only logs an error, but
-- the readings would be lost until it exists).
--
-- One row per reject capture (command_id unique): Caliban measures every
-- capture_reject photo as it arrives, whether or not the reject-vision
-- prompt is switched on, and measures older photos the first time someone
-- opens them in the Rejets tab.
--
-- larva_area_pct is a share of visible AREA. To turn it into weight -- what
-- decides reprise vs rejet -- weigh a few reject trays right after the rig
-- photographs them: pick out the larvae, weigh larvae and the rest
-- separately, and enter both in the Rejets tab (Pesée). With five or six of
-- those, the query at the bottom gives the area -> weight relation.

create table if not exists reject_colour_readings (
    id               uuid primary key default gen_random_uuid(),
    command_id       uuid not null unique references capture_commands (id),

    larva_area_pct   numeric,          -- larvae, % of the material's visible area
    material_pct     numeric,          -- material, % of the frame
    method_version   text not null,

    -- Calibration: the same tray, hand-sorted and weighed.
    weighed_larva_g  numeric,
    weighed_rest_g   numeric,
    weighed_by       uuid,
    weighed_at       timestamptz,

    created_at       timestamptz not null default now()
);

-- Caliban writes with the service key (bypasses RLS). QC+ can read, same
-- policy shape as reject_stream_readings.
alter table reject_colour_readings enable row level security;

drop policy if exists reject_colour_readings_select on reject_colour_readings;
create policy reject_colour_readings_select on reject_colour_readings
    for select to authenticated
    using (true);


-- Calibration: measured area share against weighed share, per weighed tray.
--   select c.lot_number, r.larva_area_pct,
--          round(100 * r.weighed_larva_g / nullif(r.weighed_larva_g + r.weighed_rest_g, 0), 1) as weighed_larva_pct,
--          s.larves_pct as prompt_larva_pct, s.decision as prompt_decision
--   from reject_colour_readings r
--   join capture_commands c on c.id = r.command_id
--   left join lateral (select larves_pct, decision from reject_stream_readings
--                      where command_id = r.command_id order by created_at desc limit 1) s on true
--   where r.weighed_larva_g is not null
--   order by c.completed_at;
