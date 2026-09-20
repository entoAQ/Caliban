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
paragraphs (plain language, not a changelog) and give it to Tim as a ready
INSERT to run in the Supabase SQL editor — there's no service-role key in the
local dev shell here either, so it can't be run directly:
```sql
INSERT INTO public.news_items (title, body, category)
VALUES ('<titre>', '<explication>', 'feature'); -- or 'aq' for a non-code AQ announcement
```
Say so explicitly at the end of the turn so it isn't missed.

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
