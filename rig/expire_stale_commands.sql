-- Expire capture commands too old to still match the tray.
--
-- Run once in the Supabase SQL editor, after capture_commands.sql.
--
-- A command is a request to photograph what is on the tray *now*. On
-- 2026-09-30 the Pi lost Wi-Fi for over an hour; when it came back it worked
-- through the queue oldest first and photographed whatever was on the tray at
-- 21:40 under lots queued at 20:11-20:21. Four lots got someone else's photo,
-- and the backlog kept every new request waiting behind it until SGSC gave up.
--
-- So a command not taken within EXPIRE_AFTER is failed instead of offered,
-- with a message telling the operator to retake it. That covers both a
-- pending row nobody claimed and a 'capturing' row the stale-claim reclaim
-- would otherwise hand out again long after the fact.
--
-- Same signature as before (one interval argument, defaulted) on purpose:
-- create or replace then swaps the body in place. Adding a second defaulted
-- argument would create an overload, and Caliban's argument-less rpc call
-- would become ambiguous between the two.
create or replace function claim_capture_command(stale_after interval default '2 minutes')
returns capture_commands
language plpgsql
as $$
declare
    -- Longer than any normal wait (a capture is ~20 s, the stale-claim
    -- reclaim is 2 min), short enough that the tray is still the same tray.
    expire_after constant interval := '5 minutes';
    claimed capture_commands;
begin
    update capture_commands
    set status = 'failed',
        completed_at = now(),
        error = 'Demande expirée : le banc ne l''a pas prise à temps. Reprenez la photo.'
    where status in ('pending', 'capturing')
      and requested_at < now() - expire_after;

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
