-- Let Caliban hear new capture commands as they are queued.
--
-- Run once in the Supabase SQL editor.
--
-- The rig used to ask for work every 3 s, each ask a Supabase RPC, and that
-- alone filled the free tier's 1 GB log quota (2026-09-27). Now it long-polls
-- Caliban, and Caliban (app/command_wakeup.py) subscribes to INSERTs on this
-- table through Realtime, which only sees tables in this publication.
--
-- Until this is run, Realtime never confirms the subscription, Caliban never
-- reports "listening", and the rig keeps polling every 3 s as before -- nothing
-- breaks, it just saves nothing.

do $$
begin
    if not exists (
        select 1 from pg_publication_tables
        where pubname = 'supabase_realtime'
          and schemaname = 'public'
          and tablename = 'capture_commands'
    ) then
        alter publication supabase_realtime add table capture_commands;
    end if;
end $$;
