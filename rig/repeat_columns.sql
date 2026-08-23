-- Columns for repeat-and-average estimates.
--
-- Run once in the Supabase SQL editor.
--
-- A band is a coarse answer: six buckets, so the finest distinction a single
-- call can make is one band wide. That is a real part of why 3-7% and 7-10%
-- get confused -- not only mis-seeing, but an answer format with nowhere finer
-- to put the result.
--
-- Averaging several rotations of the same photo gives a continuous number that
-- the band structure cannot express, so it needs somewhere of its own to live.
-- predicted_band stays as it was, derived from this estimate, so nothing that
-- reads the old column breaks.

alter table vision_band_estimates
    -- How many rotations were averaged. 1 means a single call, which is what
    -- every existing row is.
    add column if not exists repeat_count int not null default 1,

    -- Mean of the band midpoints across those rotations. This is the number to
    -- regress against lab ME%: it has finer resolution than the band, and it
    -- is what a calibration curve should be fitted to.
    add column if not exists estimate_pct numeric,

    -- Spread of those midpoints. Nothing about the sample changed between
    -- rotations, so this is how much the answer moved for no reason -- a more
    -- honest uncertainty than the model's self-reported confidence, which is
    -- frequently "Élevée" while wrong.
    add column if not exists estimate_spread numeric;

-- Only rows with a real value are worth comparing, and only multi-rotation
-- rows have a spread to look at.
create index if not exists vision_band_estimates_estimate_idx
    on vision_band_estimates (prompt_version, estimate_pct)
    where real_me_pct is not null;
