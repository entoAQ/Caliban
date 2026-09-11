-- Re-scores: stored, measured captures run again through one or more prompts.
--
-- Run once in the Supabase SQL editor, BEFORE using "Re-noter" on the band-test
-- page. Deploy the matching Caliban (the record switch) before using it too.
--
-- Deliberately a separate table from vision_band_estimates. A re-score is the
-- same photo judged again, not a new sample; putting it in the estimate table
-- would count one tray several times in every accuracy figure. Here each row is
-- one photo x one prompt in one run, and run_id groups a click of "Lancer".
--
-- real_me_pct is the lab value looked up fresh at re-score time, not copied
-- from the original estimate, so a lab correction made since the photo was
-- taken is what the re-score is judged against.

create table if not exists vision_rescores (
    id                 bigserial primary key,
    run_id             uuid not null,
    -- The estimate the photo came from. Text, not a foreign key: it is a
    -- pointer for tracing back, and the re-score must survive that row changing.
    source_estimate_id text,
    storage_path       text not null,
    lot_number_text    text,
    real_me_pct        numeric,
    prompt_version     text not null,
    prompt_hash        text,
    repeat_count       int,
    predicted_band     text,
    estimate_pct       numeric,
    -- One band per rotation, in rotation order.
    repeat_bands       text[],
    created_at         timestamptz not null default now(),
    created_by         uuid default auth.uid()
);

create index if not exists vision_rescores_run_idx on vision_rescores (run_id);
create index if not exists vision_rescores_prompt_idx on vision_rescores (prompt_version, real_me_pct);

alter table vision_rescores enable row level security;

drop policy if exists qc_select_rescores on vision_rescores;
create policy qc_select_rescores on vision_rescores
    for select to authenticated using (public.role_gte('qc'));

drop policy if exists qc_insert_rescores on vision_rescores;
create policy qc_insert_rescores on vision_rescores
    for insert to authenticated with check (public.role_gte('qc'));


-- Compare prompts on the same photos (all runs):
--
--   select prompt_version,
--          case when real_me_pct < 3 then '<3' when real_me_pct < 8 then '3-8' else '8+' end as reel,
--          count(*) as n,
--          round(avg(estimate_pct)::numeric, 2) as estimation_moy,
--          round(avg(real_me_pct)::numeric, 2)  as reel_moy,
--          sum((predicted_band = '<3%')::int)   as lu_moins_3
--   from vision_rescores
--   group by 1, 2
--   order by 2, 1;
