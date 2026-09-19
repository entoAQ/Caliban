-- Move live traffic off the last two DENSITE-asking prompts.
--
-- Run once in the Supabase SQL editor, after prompt_3_2b.sql and
-- prompt_3_2b_plastique.sql (both updated 2026-09-19 to drop DENSITE).
--
-- Before this: rig_prompt_variant was '3.2' and operator_settings.low_variant
-- was '3.2b' -- both still asked DENSITE, a field that correlated with
-- measured density at r = 0.19 (rig/prompt_3_3_draft.sql) and was dropped
-- from every variant since 3.3. high_variant ('3.4b') was already clean.
--
-- '3.2' itself is untouched (see app/main.py: this variant family never
-- rewrites a prompt in place) -- '3.2c' is 3.2 with only DENSITE removed,
-- added as a new label the same way 3.1 -> 3.2 was. low_variant keeps the
-- label '3.2b'; that row's own text was updated in place instead, per the
-- db-sourced side of this prompt family's own convention.
--
-- Density estimation now runs through a separate instrument entirely (the
-- bench rig's time-of-flight sensor -- see tof_density_readings), so there
-- is nothing left for either prompt to ask.

update public.system_config
set value = '3.2c'
where key = 'rig_prompt_variant';

-- VERIFY -- run on its own:
--
--   select key, value from public.system_config
--    where key in ('rig_prompt_variant', 'operator_settings');
--
-- rig_prompt_variant should read 3.2c. operator_settings.low_variant stays
-- '3.2b' in the JSON -- that label is unchanged, only what it points to.
