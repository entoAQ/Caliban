-- Widen vision_prompts.band_scale's check constraint to allow 'coarse6'
-- (app/main.py's new six-band scale, <3/3-5/5-8/8-10/10-13/>13 -- see
-- rig/prompt_3_2b_bands6.sql and rig/prompt_3_4b_bands6.sql).
--
-- Run this BEFORE either of those two files, in the Supabase SQL editor --
-- their upserts fail vision_prompts_band_scale_check otherwise, since the
-- table was created with only 'standard' and 'coarse' allowed
-- (rig/prompt_variants.sql).

alter table vision_prompts
    drop constraint if exists vision_prompts_band_scale_check;

alter table vision_prompts
    add constraint vision_prompts_band_scale_check
    check (band_scale in ('standard', 'coarse', 'coarse6'));
