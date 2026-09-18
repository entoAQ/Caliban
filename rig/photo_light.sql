-- How bright each analysed photo was, kept with the estimate.
--
-- Run once in the Supabase SQL editor. Caliban fills the columns from the next
-- capture; until then rows are saved without them, so the order of deploy and
-- script does not matter. Existing photos can be measured afterwards with
-- POST /admin/photo-stats/backfill (AQ and up).
--
-- Why: exposure and gain are fixed at calibration, so if the bench light is
-- turned down afterwards every photo gets darker -- and a darker tray reads as
-- more MEO. On 2026-09-15 around midday five lots were called 8%+ while the lab
-- measured 4.5-6.9%, and the photos of that stretch look dimmer. Without the
-- brightness recorded there is no way to tell that from real contamination.
--
-- photo_mean is the average brightness of the analysed frame (0-255) and
-- photo_std how much variation there is in it -- the same two numbers the
-- black-frame check already measures before any call is made.

alter table public.vision_band_estimates
    add column if not exists photo_mean numeric,
    add column if not exists photo_std  numeric;

create index if not exists vision_band_estimates_photo_mean_idx
    on public.vision_band_estimates (created_at)
    where photo_mean is not null;


-- Brightness by day, operator captures (after a backfill):
--   select (created_at at time zone 'America/Toronto')::date as jour,
--          count(*) filter (where photo_mean is not null) as mesurees,
--          round(avg(photo_mean)::numeric, 1) as moyenne,
--          round(min(photo_mean)::numeric, 1) as min,
--          round(max(photo_mean)::numeric, 1) as max
--   from vision_band_estimates
--   where source = 'operator' and operator_instruction is not null
--   group by 1 order by 1;
--
-- Does a darker photo read higher? (pair each capture's brightness with its estimate)
--   select width_bucket(photo_mean, 60, 200, 7) as tranche,
--          round(avg(photo_mean)::numeric, 0) as luminosite,
--          count(*) as captures,
--          round(avg(estimate_pct)::numeric, 2) as estimation_moy
--   from vision_band_estimates
--   where source = 'operator' and photo_mean is not null and prompt_version = '3.4b'
--   group by 1 order by 1;
