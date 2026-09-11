-- Plastic check: where its answer is recorded.
--
-- Run once in the Supabase SQL editor, BEFORE using prompt 3.2b-p
-- (rig/prompt_3_2b_plastique.sql). Caliban only writes these columns when the
-- prompt actually asked about plastic, so every other variant keeps working
-- whether or not this has been run.
--
-- A null plastic_flag means the prompt did not ask -- not that there was no
-- plastic. Only rows from a plastic-asking prompt carry a verdict.

alter table vision_band_estimates
    -- True if ANY rotation answered oui. The tray gets checked by a person.
    add column if not exists plastic_flag  boolean,
    -- One answer per rotation, in rotation order: oui / non / incertain.
    add column if not exists plastic_votes text[],
    -- What the model said it saw and where, from a rotation that said oui.
    add column if not exists plastic_desc  text;

-- The same on re-scores, so the plastic check can be tested on stored photos:
-- a flag there is a false alarm, because none of those trays was spiked.
alter table vision_rescores
    add column if not exists plastic_flag  boolean,
    add column if not exists plastic_votes text[],
    add column if not exists plastic_desc  text;


-- False alarms on stored (plastic-free) photos, per prompt:
--
--   select prompt_version, count(*) as photos,
--          sum(plastic_flag::int) as signales,
--          sum((array_position(plastic_votes, 'incertain') is not null)::int) as incertains
--   from vision_rescores
--   where plastic_votes is not null
--   group by 1;
