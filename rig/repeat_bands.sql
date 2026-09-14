-- Every rotation's band, kept with the estimate.
--
-- Run once in the Supabase SQL editor. Caliban writes the column from the
-- capture after this is run; until then it saves rows without it, so the order
-- of deploy and script does not matter.
--
-- Why: the estimate is the mean of fixed band values (<3% = 1.5, 3-8% = 5.5,
-- 8-13% = 10.5, >13% = 16). Those values are a guess, and the 2026-09-13 lots
-- suggest they are off -- lots whose readings were mostly <3% measured 4-6% in
-- the lab. Fitting better values per prompt against lot results needs how each
-- sample's rotations split across the bands, not just their average.
--
-- Also fills the column for existing rows, from the readings Caliban already
-- writes at the start of the justification:
--   "[8 rotations : 8-13%, 3-8%, ... -> 8.6%] ..."
-- and, for single-rotation rows, from the predicted band itself.

alter table public.vision_band_estimates
    add column if not exists repeat_bands text[];

update public.vision_band_estimates
set repeat_bands = string_to_array(substring(justification from '^\[\d+ rotations : (.*?) -> '), ', ')
where repeat_bands is null
  and justification ~ '^\[\d+ rotations : .*? -> ';

update public.vision_band_estimates
set repeat_bands = array[predicted_band]
where repeat_bands is null
  and coalesce(repeat_count, 1) = 1
  and predicted_band is not null;


-- Check: how operator captures' rotations split, per prompt.
--   select prompt_version, b as bande, count(*)
--   from vision_band_estimates, unnest(repeat_bands) as b
--   where source = 'operator'
--   group by 1, 2 order by 1, 2;
