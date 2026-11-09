# CLAUDE.md

Guidance for Claude Code (and anyone else) working in this repo.

## Ship a news item with any user-facing change

SGSC (the main app, separate repo) has an in-app news popup for communicating
changes to QC/QA floor staff — `news_items`/`news_reads` tables, added
2026-09-20, in the **same Supabase project** this repo already writes to via
`supabase_client.py` (service-role, bypasses RLS). Caliban has no UI of its
own, but a change here can still be user-visible — a new capture prompt, a
changed destoner threshold, a fixed false-positive rate — and someone on the
floor has no way to know unless it's announced there.

When a change here would matter to someone using the app (not internal
refactors/infra), write the item as a French title + one or two short
paragraphs (plain language, not a changelog). This repo has no `database/`
queue of its own — sgsc-app does (`database/_pending-news.jsonl`, run via
`node scripts/run-pending-news.mjs`, see that repo's CLAUDE.md) — so give Tim
the one JSON line to append there himself:
```json
{"title": "<titre>", "body": "<explication>", "category": "feature"}
```
`category` is `'feature'` for app-visible changes, `'aq'` for a non-code AQ
announcement. Say so explicitly at the end of the turn so it isn't missed —
still manual on purpose (Tim reviews before anything goes live), just no raw
SQL to write.

## Read the schema from the snapshot, and check it is current

This repo has no schema of its own. The one place to read the Supabase schema
from is `database/schema-snapshot.sql` in sgsc-app, not the `rig/*.sql` files (they
show a change, not the result) and not a guess from what the code reads.

Before relying on it, `git pull` sgsc-app and check line 8, which says when its
dump was taken. If that is more than 2 days old, say so and fix that first (see
sgsc-app's `database/README.md`). Even when current it is up to a day behind, so
for anything changed since the dump, or when a wrong column would be costly, check
production read-only through `claude_ro` (`SGSC_RO_DB_URL`).

## Policies written here follow sgsc-app's RLS rules

The `rig/*.sql` files create roles, grants and RLS policies on the same Supabase
database as sgsc-app, so the rules in sgsc-app's CLAUDE.md ("Postgres
access-control rules") apply to every one of them. Since 2026-11-09
(`database/2026-11-09-rls-initplan-and-policy-cleanup.sql` there) that means in
particular:

- A rig policy names its role (`TO ariel_rig`, `TO tray_rig`); never `TO PUBLIC`.
  The rig logins are not members of `authenticated` and carry no JWT, so
  `auth.uid()` / `auth.role()` are NULL for them: a predicate on those can never
  admit a rig, and a `TO authenticated` policy never reaches one.
- Any `auth.uid()`, `role_gte()` or `get_user_role()` in a policy is wrapped:
  `(select auth.uid())`, `(select public.role_gte('qc'))`. Bare, it runs once per
  row.
- No `FOR ALL` on a table that also has a read policy (split it into insert /
  update / delete), and never two permissive policies for the same table, role
  and command: widen or OR-merge the existing one instead.

After running any file that adds or changes a policy, run sgsc-app's
`database/checks/2026-11-09-rls-policy-shape.sql`; it must return no rows.

## The App Service is retired: Ariel's rules live in `rig/inline/decide.py`

Since 2026-11-02 Ariel decides on its own Pi (`ARIEL_DECIDES=1`), and on
2026-11-09 both rigs' `CALIBAN_URL` lines came out and the App Service was
stopped, to be deleted. Nothing in production runs `app/` any more.

So **a change to Ariel's decisions is made in `rig/inline/decide.py` only.**
It began as a copy generated from `app/main.py`, and until now changes were
made in both, in step; that stops here. Don't edit `app/main.py`'s inline
routes to change behaviour on the line -- nothing reads them -- and don't
regenerate `decide.py` from `main.py`, which would undo every change made
since. Check a rule change by replaying it over recorded readings
(`vision_band_estimates`, `source = 'inline'`) before it ships: the Pi pulls
`main` within 5 minutes of a push.

`loop.py` and `doctor.py` still have their `CALIBAN_URL` branches, as the way
back while the App Service is only stopped. They come out with `app/`, the
Dockerfile and the publish workflow once it is deleted.

## Vision prompt language

**Every vision prompt's instructions are written in English.** Only the
fields that are genuinely operator- or QC-facing output ask for French
explicitly in their own field description -- `JUSTIFICATION: [one sentence,
in French, ...]`, `PLASTIQUE: [oui, non, ou incertain]`, and similar. The
model itself is instructed in English throughout.

This is already the convention in every prompt in `rig/prompt_*.sql` and in
`app/main.py`'s `BAND_PROMPT_VARIANTS` and `density_vision_prompt()` -- this
file exists to make it explicit rather than something a new prompt has to
infer by pattern-matching the others.

## Prompt versioning

A prompt is never rewritten in place. A wording change gets a new label
(`tof-v1` -> `tof-v2`, `3.2` -> `3.2c`, etc.), so a row's recorded
`prompt_version`/`prompt_hash` stays interpretable against the text that
actually produced it. See the comment above `_P31` in `app/main.py` for the
fuller reasoning, and the header of any `rig/prompt_*.sql` file for the
db-sourced side of the same rule (there, editing in place is fine --
`prompt_hash` and `vision_prompt_history` already handle traceability, so
that side of the family updates its `vision_prompts` row directly instead of
minting a new label for every wording tweak).
