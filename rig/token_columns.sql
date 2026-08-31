-- Token counts per estimate, so the cost of a rotation is measured.
--
-- Run once in the Supabase SQL editor.
--
-- Rotations multiply cost exactly. GPT-4o scales any image to fit 2048 and
-- tiles it, so a rotated frame costs the same as the original -- four
-- rotations is four times the tokens, with image tokens dominating and the
-- prompt itself a rounding error beside them. That makes "is the extra
-- resolution worth the calls" an arithmetic question rather than an argument,
-- but only if the numbers are recorded.
--
-- Summed across rotations rather than averaged: this is what the whole
-- estimate cost, which is the figure that decides how many rotations to run.

alter table vision_band_estimates
    add column if not exists prompt_tokens int,
    add column if not exists completion_tokens int;

-- Cost per estimate, by prompt and by rotation count. Fill in your own rate
-- per million tokens; Azure's differs by region and agreement, so hardcoding
-- one here would be a number that looks authoritative and is not.
--
--   select prompt_version, repeat_count,
--          count(*) as n,
--          round(avg(prompt_tokens))     as tokens_in_moy,
--          round(avg(completion_tokens)) as tokens_out_moy,
--          round(sum(prompt_tokens) / 1e6 * <rate_in>
--              + sum(completion_tokens) / 1e6 * <rate_out>, 2) as cout_total
--   from vision_band_estimates
--   where prompt_tokens is not null
--   group by prompt_version, repeat_count
--   order by prompt_version, repeat_count;
