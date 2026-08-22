-- Command queue for the bench rig camera.
--
-- Run once in the Supabase SQL editor.
--
-- Why a queue rather than SGSC calling the Pi directly: it cannot. The Pi
-- sits on a plant LAN behind NAT with no public address, the SGSC page is
-- served over HTTPS so a request to a plain-HTTP local device is blocked as
-- mixed content, and Chrome's Private Network Access rules block it again
-- even if it were not. Every path inward fails. So the Pi reaches outward
-- instead, polls for work, and does it -- the same principle that makes Pi
-- Connect work from inside the plant.

create table if not exists capture_commands (
    id            uuid primary key default gen_random_uuid(),

    -- Both the id and the human-readable number. The id is the real link;
    -- the number is what the operator sees and what the Pi puts in the
    -- filename, so a photo remains identifiable even outside the database.
    lot_id        uuid,
    lot_number    text not null,

    status        text not null default 'pending'
                  check (status in ('pending', 'capturing', 'done', 'failed')),

    requested_by  uuid references auth.users (id),
    requested_at  timestamptz not null default now(),
    claimed_at    timestamptz,
    completed_at  timestamptz,

    -- Storage paths, one per band. The IR frame is optional: the rig can be
    -- run without the IR boards attached, and a visible-only capture is
    -- still a complete result.
    image_path    text,
    ir_image_path text,

    error         text
);

-- The poller's only query: oldest pending command. Partial index because
-- pending rows are a tiny and short-lived fraction of the table.
create index if not exists capture_commands_pending_idx
    on capture_commands (requested_at)
    where status = 'pending';

-- SGSC polls this while the operator waits.
create index if not exists capture_commands_lot_idx
    on capture_commands (lot_id, requested_at desc);


-- Claim exactly one command, atomically.
--
-- FOR UPDATE SKIP LOCKED means two pollers -- or one poller whose previous
-- request has not finished timing out -- can never take the same row. That
-- costs nothing with a single rig and means adding a second one later needs
-- no changes here.
--
-- It also reclaims commands stuck in 'capturing': if the Pi loses power
-- mid-capture, the row would otherwise sit claimed forever and the operator
-- would wait for a photo that is never coming. Anything held longer than
-- the timeout is assumed abandoned and offered again.
create or replace function claim_capture_command(stale_after interval default '2 minutes')
returns capture_commands
language plpgsql
as $$
declare
    claimed capture_commands;
begin
    select * into claimed
    from capture_commands
    where status = 'pending'
       or (status = 'capturing' and claimed_at < now() - stale_after)
    order by requested_at
    for update skip locked
    limit 1;

    if not found then
        return null;
    end if;

    update capture_commands
    set status = 'capturing',
        claimed_at = now()
    where id = claimed.id
    returning * into claimed;

    return claimed;
end;
$$;


-- Row level security.
--
-- The Pi never talks to Supabase directly -- it goes through Caliban, which
-- holds the service key and bypasses RLS. Putting a Supabase key on plant
-- hardware would mean a credential sitting on a device in a factory with no
-- way to scope it to the one table it needs.
alter table capture_commands enable row level security;

-- Signed-in SGSC users queue captures and watch their progress. Nobody
-- updates rows from the browser; only the rig completes a command.
create policy capture_commands_insert on capture_commands
    for insert to authenticated
    with check (requested_by = auth.uid());

create policy capture_commands_select on capture_commands
    for select to authenticated
    using (true);
