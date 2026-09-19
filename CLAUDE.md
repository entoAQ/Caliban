# CLAUDE.md

Guidance for Claude Code (and anyone else) working in this repo.

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
