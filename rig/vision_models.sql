-- Re-scores by model: which model produced each re-score, and what it cost.
--
-- Run once in the Supabase SQL editor, BEFORE comparing models on the re-score
-- tab. Without it the tab still runs, but every row fails to save and says so.
--
-- model is the label chosen on the tab ("default", or one from Caliban's
-- VISION_MODELS). model_actual is what the provider reported answering --
-- for Azure that carries the version behind the deployment name, e.g.
-- "gpt-4o-2024-11-20", so the default model's real version is visible here too.
-- Existing rows have no model: they were all the default.

alter table vision_rescores
    add column if not exists model             text,
    add column if not exists model_actual      text,
    -- All rotations together: what one photo cost.
    add column if not exists prompt_tokens     int,
    add column if not exists completion_tokens int;

create index if not exists vision_rescores_model_idx on vision_rescores (model, prompt_version);


-- Compare models on the same photos, by true class:
--
--   select coalesce(model, 'default') as modele, prompt_version,
--          case when real_me_pct < 3 then '<3' when real_me_pct < 8 then '3-8' else '8+' end as reel,
--          count(*) as n,
--          sum((case when real_me_pct >= 8 then predicted_band in ('8-13%', '>13%')
--                    else predicted_band in ('<3%', '3-8%') end)::int) as bon_cote_de_8,
--          sum((predicted_band = '<3%')::int) as lu_moins_3,
--          round(avg(estimate_pct)::numeric, 2) as estimation_moy,
--          round(avg(prompt_tokens + completion_tokens)) as jetons_photo
--   from vision_rescores
--   group by 1, 2, 3
--   order by 3, 1, 2;
--
-- The version behind the default deployment:
--
--   select model_actual, count(*) from vision_rescores
--   where coalesce(model, 'default') = 'default' and model_actual is not null
--   group by 1;
