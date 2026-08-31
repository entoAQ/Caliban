-- The column the backfill has been failing on, plus a catch-up for the rows
-- it never managed to fill.
--
-- Run once in the Supabase SQL editor.
--
-- What happened: /band-estimates/backfill writes real_pct_source, that column
-- was never created, so every backfill errored. The frontend ignores backfill
-- failures on purpose -- a save must not look broken because a follow-up step
-- failed -- so it failed silently, every time, for weeks. Fourteen captures on
-- 30 August have estimates and no real values at all.
--
-- The lesson is not "add the column". It is that a step which is allowed to
-- fail quietly needs somewhere its failures accumulate visibly, and
-- real_pct_source is now that place: null with a lot_id means the lookup never
-- ran, which is a different problem from a lot that has no results yet.

alter table vision_band_estimates
    add column if not exists real_pct_source text;

-- Catch-up. Fills every estimate whose lot now has both components, using the
-- same arithmetic as the service: ME% is not stored anywhere, it is computed
-- from me_organic_wt and bulk_density.
--
-- Only rows still missing the value are touched. An estimate that already has
-- one is never rewritten -- if a lab result is later corrected, propagating
-- that is a decision for a person, not a side effect of running a script.
with vals as (
    select
        tr.lot_id,
        max(case when td.code = 'me_organic_wt' then tr.result_value end) as wt,
        max(case when td.code = 'bulk_density'  then tr.result_value end) as dens
    from test_results tr
    join test_definitions td on td.id = tr.test_id
    where td.code in ('me_organic_wt', 'bulk_density')
      and tr.is_superseded = false
    group by tr.lot_id
)
update vision_band_estimates e
set real_me_pct     = round((v.wt / v.dens) * 100, 2),
    real_density    = v.dens,
    real_pct_source = 'lot_lookup_catchup'
from vals v
where e.lot_id = v.lot_id
  and v.wt is not null
  and v.dens is not null
  and v.dens > 0
  and e.real_me_pct is null;

-- What is left, and why. Anything still null here has no lab result yet, or
-- was never linked to a lot in the first place.
select
    count(*) filter (where real_me_pct is not null)                  as with_real,
    count(*) filter (where real_me_pct is null and lot_id is not null) as lot_but_no_result,
    count(*) filter (where lot_id is null)                            as no_lot_at_all
from vision_band_estimates;
