-- Prompt variants, editable without a deploy.
--
-- Run once in the Supabase SQL editor.
--
-- Every prompt currently lives in app/main.py, so changing a paragraph of
-- French costs a container build, an image push, a pull and a cold start. That
-- is a strange price for editing text, and it is the single thing most likely
-- to be edited during a week of prompt work.
--
-- This table takes precedence over the in-code dict, which stays as the
-- fallback: an empty table, an unreachable database or a bad edit all leave
-- the service running exactly what it runs today. Nothing here is required for
-- Caliban to work.
--
-- What does NOT change is provenance. prompt_hash is computed from the text
-- actually sent, so editing a row changes the hash of everything recorded
-- afterwards while leaving earlier rows interpretable against the text that
-- produced them. Two estimates with the same label and different hashes are
-- correctly distinguishable.

create table if not exists vision_prompts (
    -- The label the operator picks and every estimate records: "3.2".
    label        text primary key,

    prompt_text  text not null,

    -- Which band vocabulary this prompt answers in. The prompt lists its own
    -- permitted labels, so the scale is a property of the text and not of the
    -- service -- get this wrong and the model's answers are read against the
    -- wrong boundaries, silently.
    band_scale   text not null default 'standard'
                 check (band_scale in ('standard', 'coarse')),

    -- Few-shot reference photos, opt-in per variant. Deliberately not a
    -- default: a new prompt gets no references unless somebody decides it
    -- should, and the reference set on file was shot on the old rig.
    uses_references boolean not null default false,

    -- Hidden from the picker without being deleted, so an old image can still
    -- be re-scored against the prompt that produced it.
    archived     boolean not null default false,

    notes        text,
    updated_at   timestamptz not null default now(),
    updated_by   uuid references auth.users (id)
);

-- Editing a prompt in place is allowed, because the alternative is a new label
-- for every wording tweak and nobody would keep that up. But the previous text
-- must not simply vanish: a row here is the only record of what a given
-- prompt_hash actually said.
create table if not exists vision_prompt_history (
    id           bigserial primary key,
    label        text not null,
    prompt_text  text not null,
    band_scale   text,
    uses_references boolean,
    archived     boolean,
    replaced_at  timestamptz not null default now()
);

create or replace function archive_vision_prompt() returns trigger as $$
begin
    insert into vision_prompt_history (label, prompt_text, band_scale, uses_references, archived)
    values (old.label, old.prompt_text, old.band_scale, old.uses_references, old.archived);
    return coalesce(new, old);
end;
$$ language plpgsql;

drop trigger if exists vision_prompts_archive on vision_prompts;
create trigger vision_prompts_archive
    before update or delete on vision_prompts
    for each row execute function archive_vision_prompt();

alter table vision_prompts enable row level security;
alter table vision_prompt_history enable row level security;

-- Readable by any signed-in user; the service reads it on every call. Writes
-- are deliberately left to the service role only -- editing a prompt changes
-- what every future estimate means, and that belongs in the SQL editor with a
-- note attached rather than behind a button somebody can reach by accident.
drop policy if exists "authenticated read prompts" on vision_prompts;
create policy "authenticated read prompts"
    on vision_prompts for select to authenticated using (true);

drop policy if exists "authenticated read prompt history" on vision_prompt_history;
create policy "authenticated read prompt history"
    on vision_prompt_history for select to authenticated using (true);

-- To add or edit a variant:
--
--   insert into vision_prompts (label, prompt_text, band_scale, notes)
--   values ('3.3', 'You are looking at ...', 'coarse', 'anchored density, tighter low end')
--   on conflict (label) do update
--     set prompt_text = excluded.prompt_text,
--         band_scale  = excluded.band_scale,
--         notes       = excluded.notes,
--         updated_at  = now();
--
-- The service picks it up within a minute; no build, no restart.
