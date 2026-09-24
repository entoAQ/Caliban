-- Round 2. The first check (rig/check_lot_average_bug.sql) ruled out the
-- two-prompt operator pairing as the cause: the decider row is usually LOWER
-- than the 2-row average, so naively averaging both rows would read HIGH, not
-- low -- opposite of what you're seeing. Also all rows there were OP- operator
-- samples, which (per project notes) aren't attached to real lots yet -- so
-- the "par lot" tab can't be reading operator data for its lot grouping.
--
-- This round looks at rows tied to a REAL lot (lot_id is not null / lot_number_text
-- matches an actual lots.lot_number), which is what a "par lot" screen would have
-- to be grouping on, and checks for the most common cause of a too-low average:
-- multiple rows per photo (one per prompt_version/variant tested) getting
-- averaged together, including variants that were only run for comparison/
-- calibration purposes rather than as the production reading.

-- 1) For each real lot, how many rows exist, how many distinct prompt_versions
-- contributed, and how the average shifts if you restrict to one prompt_version.
select
    v.lot_number_text,
    l.lot_number,
    count(*)                                   as rows_total,
    count(distinct v.prompt_version)            as distinct_prompt_versions,
    array_agg(distinct v.prompt_version)        as prompt_versions,
    array_agg(distinct v.source)                as sources,
    array_agg(distinct v.is_training)           as is_training_values,
    round(avg(v.estimate_pct)::numeric, 2)      as avg_all_rows
from vision_band_estimates v
join lots l on l.id = v.lot_id
group by v.lot_number_text, l.lot_number
order by rows_total desc
limit 50;

-- 2) Row-level detail for ONE real lot -- replace 'YOUR-LOT-NUMBER'. This shows
-- every row Postgres has for it: which prompt_version, which source, whether it
-- was a training/reference row (should be excluded from a production average),
-- and the estimate itself, so you can compare directly against whatever number
-- the "par lot" tab is showing for this same lot.
select
    v.id,
    v.created_at,
    v.source,
    v.is_training,
    v.prompt_version,
    v.predicted_band,
    v.estimate_pct,
    v.repeat_count,
    v.escalated,
    v.error_flagged
from vision_band_estimates v
join lots l on l.id = v.lot_id
where l.lot_number = 'YOUR-LOT-NUMBER'
order by v.created_at;

-- 3) Same lot, three candidate averages side by side, so we can see which one
-- (if any) matches what the tab currently shows:
--   a) every row, no filtering (what a naive "select avg" would give)
--   b) production only: excludes is_training rows and any row from a
--      non-default/comparison prompt_version
--   c) most-recent-prompt-version only: avg restricted to whichever
--      prompt_version has the most rows for this lot (a proxy for "the
--      prompt actually in production" if several were tested on the same lot)
with rows as (
    select v.*
    from vision_band_estimates v
    join lots l on l.id = v.lot_id
    where l.lot_number = 'YOUR-LOT-NUMBER'
),
top_variant as (
    select prompt_version
    from rows
    group by prompt_version
    order by count(*) desc
    limit 1
)
select
    round(avg(estimate_pct) filter (where true)::numeric, 2)
        as avg_every_row,
    round(avg(estimate_pct) filter (where is_training = false)::numeric, 2)
        as avg_excl_training,
    round(avg(estimate_pct) filter (
        where is_training = false
          and prompt_version = (select prompt_version from top_variant)
    )::numeric, 2)
        as avg_top_variant_only,
    count(*) as rows_total,
    count(*) filter (where is_training = false) as rows_excl_training
from rows;
