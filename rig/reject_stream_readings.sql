-- Vision reading for a reject-stream photo -- rework/reject prompt, draft v1.
--
-- Run once in the Supabase SQL editor.
--
-- Separate table rather than new columns on capture_commands, same
-- reasoning as tof_density_readings: this is a second, optional reading of
-- a capture, not part of what the capture itself is, and it can be dropped
-- or refit without touching the queue row.

create table if not exists reject_stream_readings (
    id                uuid primary key default gen_random_uuid(),
    command_id        uuid references capture_commands (id),

    larves            text,
    matiere_etrangere text,
    confiance         text,
    decision          text,
    justification     text,

    raw_response      text,
    prompt_version    text,

    created_at        timestamptz not null default now()
);

create index if not exists reject_stream_readings_command_idx
    on reject_stream_readings (command_id);

-- Caliban writes with the service key (bypasses RLS, same as every other
-- table it owns). QC+ can read, same policy shape as capture_commands.
alter table reject_stream_readings enable row level security;

create policy reject_stream_readings_select on reject_stream_readings
    for select to authenticated
    using (true);
