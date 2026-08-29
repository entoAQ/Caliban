-- Columns for the vision bulk-density estimate.
--
-- Run once in the Supabase SQL editor.
--
-- Be clear about what is being stored. A top-down photo of a thin single
-- layer carries very little direct information about bulk density, which is a
-- property of packing and moisture -- and a scattered monolayer shows
-- neither. What the photo does carry is correlates: larva size, how plump or
-- shrivelled they look, the fraction of fines.
--
-- So this is not a measurement. It is a reading from an instrument of unknown
-- calibration, recorded so that it can be regressed against real densities
-- later. The question it has to answer first is not "is it right" but "is it
-- consistent" -- an instrument that reads consistently wrong can be
-- calibrated, and one that reads inconsistently cannot be fixed at all.

alter table vision_band_estimates
    -- Grams per litre, averaged across rotations. Whole BSF larvae sit around
    -- 400-700, and anything outside 100-1500 is rejected before it gets here:
    -- a unit slip left in the data would poison the very mean the calibration
    -- depends on.
    add column if not exists density_est numeric,

    -- Spread of that estimate across rotations of the same photograph.
    -- Nothing about the sample changed between them, so this is how much the
    -- answer moved for no reason. It is the number that decides whether the
    -- idea is worth pursuing: a wide spread means there is nothing stable to
    -- calibrate, and the honest response is to drop the approach rather than
    -- tune it.
    add column if not exists density_spread numeric;

-- The pairs worth regressing: an estimate and a real value on the same row.
create index if not exists vision_band_estimates_density_idx
    on vision_band_estimates (prompt_version, density_est)
    where density_est is not null;

-- The lab's measured bulk density, attached to the estimate for comparison.
--
-- Already fetched when computing ME% (me_organic_wt / bulk_density * 100) and
-- then discarded. Keeping it costs nothing and it is the ground truth the
-- model's estimate is judged against -- in the same g/L, on the same physical
-- sample, on the same row. Without it every density_est is a number with
-- nothing to compare against, and the comparison would have to be assembled by
-- hand from two tables afterwards.
alter table vision_band_estimates
    add column if not exists real_density numeric;

-- The pairs worth regressing.
create index if not exists vision_band_estimates_density_pair_idx
    on vision_band_estimates (prompt_version)
    where density_est is not null and real_density is not null;
