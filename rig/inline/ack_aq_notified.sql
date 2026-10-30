-- Inline rig: "AQ avisée" answers the "aviser l'AQ" screen (2026-10-30).
-- Run BEFORE the push that adds it: until then Caliban's inserts of that choice
-- would fail this check, and Ariel would keep resending the batch.

alter table inline_acks drop constraint if exists inline_acks_choice_check;
alter table inline_acks add constraint inline_acks_choice_check
    check (choice in ('adjusted', 'clean_rejects', 'set', 'aq_notified'));
