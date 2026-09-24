-- Compares the lot average two ways:
--   a) mean_estimate  -- what operator_lot_summary currently reports:
--                         avg(estimate_pct), the rotation-level midpoint average
--                         per SAMPLE, then averaged again across the lot's samples.
--   b) mean_midpoint  -- avg of the reported BAND's midpoint per sample (coarse
--                         scale: <3% -> 1.5, 3-8% -> 5.5, 8-13% -> 10.5, >13% -> 16.0),
--                         i.e. what a quick manual band-based calc gives.
-- against lot_me_pct (the lab ME%, ground truth), so we can see which one tends
-- to land closer to reality rather than just guessing from a handful of lots.
--
-- Run in the Supabase SQL editor. operator_sample_lots / operator_lot_summary
-- must already exist (database/2026-09-14-operator-sample-lots.sql in sgsc-app).

with band_midpoint as (
    select *
    from (values
        ('<3%',  1.5),
        ('3-8%', 5.5),
        ('8-13%', 10.5),
        ('>13%', 16.0)
    ) as t(band, midpoint)
),
per_sample as (
    select
        osl.lot_id,
        osl.lot_number,
        osl.sample_id,
        osl.sampled_at,
        osl.estimate_pct,
        bm.midpoint
    from operator_sample_lots osl
    left join band_midpoint bm on bm.band = osl.predicted_band
),
per_lot as (
    select
        lot_id,
        lot_number,
        count(*)                                    as samples,
        round(avg(estimate_pct)::numeric, 2)         as mean_estimate,
        round(avg(midpoint)::numeric, 2)             as mean_midpoint
    from per_sample
    group by lot_id, lot_number
)
select
    pl.lot_number,
    pl.samples,
    pl.mean_estimate,
    pl.mean_midpoint,
    ols.lot_me_pct,
    round(abs(pl.mean_estimate - ols.lot_me_pct)::numeric, 2)  as err_estimate,
    round(abs(pl.mean_midpoint - ols.lot_me_pct)::numeric, 2)  as err_midpoint,
    case
        when ols.lot_me_pct is null then null
        when abs(pl.mean_estimate - ols.lot_me_pct) < abs(pl.mean_midpoint - ols.lot_me_pct) then 'estimate_pct closer'
        when abs(pl.mean_estimate - ols.lot_me_pct) > abs(pl.mean_midpoint - ols.lot_me_pct) then 'midpoint closer'
        else 'tie'
    end as which_is_closer
from per_lot pl
join operator_lot_summary ols using (lot_id, lot_number)
order by ols.packaged_at desc;

-- Summary across every lot with a lab value: which method wins on average, and how often.
with band_midpoint as (
    select *
    from (values
        ('<3%',  1.5),
        ('3-8%', 5.5),
        ('8-13%', 10.5),
        ('>13%', 16.0)
    ) as t(band, midpoint)
),
per_sample as (
    select
        osl.lot_id,
        osl.estimate_pct,
        bm.midpoint
    from operator_sample_lots osl
    left join band_midpoint bm on bm.band = osl.predicted_band
),
per_lot as (
    select
        lot_id,
        avg(estimate_pct) as mean_estimate,
        avg(midpoint)     as mean_midpoint
    from per_sample
    group by lot_id
),
scored as (
    select
        pl.lot_id,
        abs(pl.mean_estimate - ols.lot_me_pct) as err_estimate,
        abs(pl.mean_midpoint - ols.lot_me_pct) as err_midpoint
    from per_lot pl
    join operator_lot_summary ols using (lot_id)
    where ols.lot_me_pct is not null
)
select
    count(*)                                                 as lots_scored,
    round(avg(err_estimate)::numeric, 3)                     as mean_abs_err_estimate,
    round(avg(err_midpoint)::numeric, 3)                     as mean_abs_err_midpoint,
    count(*) filter (where err_estimate < err_midpoint)      as lots_estimate_closer,
    count(*) filter (where err_midpoint < err_estimate)      as lots_midpoint_closer,
    count(*) filter (where err_estimate = err_midpoint)      as lots_tied
from scored;
