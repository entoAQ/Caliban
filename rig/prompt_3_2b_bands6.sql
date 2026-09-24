-- Prompt 3.2b -- band scale narrowed from 4 bands to 6, in steps of
-- roughly 3 (coarse -> coarse6 in app/main.py's BAND_SCALES). Only the
-- BANDE list and the low-end paragraph change; everything else is the
-- current 3.2b text word for word.
--
-- Why: the "coarse" scale (<3%, 3-8%, 8-13%, >13%) widened bands to give
-- the model a question it could answer consistently, but that left the
-- two boundaries the operator screen actually acts on (below 3%, at/above
-- 8%) sharing a bucket with a lot of headroom -- a 4% tray and a 7.9% tray
-- both read "3-8%" and get the same "aucun changement" instruction. AQ
-- asked for finer resolution in steps of 3: <3%, 3-5%, 5-8%, 8-10%,
-- 10-13%, >13%. The <3% and 8%+ boundaries this screen keys off
-- (OP_DECREASE_MAX, OP_INCREASE_MIN) are unchanged -- 8-10%/10-13% split
-- the old "8-13%" bucket without moving where AUGMENTER starts; that
-- split exists for a possible future AQ severity tier on the operator
-- screen (SGSC), not used by this prompt or by Caliban's own logic yet.
--
-- Editing in place, not a new label: this is the db-sourced side of the
-- prompt family, where that is the established practice (see the header
-- of prompt_3_2b.sql for the fuller reasoning) -- prompt_hash and
-- vision_prompt_history already keep earlier estimates interpretable
-- against the text that produced them.
--
-- band_scale changes from 'coarse' to 'coarse6' (app/main.py). Existing
-- vision_band_estimates rows recorded under the old 4-band text keep
-- their recorded band_scale via prompt_hash lookup and are unaffected;
-- only estimates produced by this text going forward read against
-- coarse6.
--
-- Check first that 3.2b is not overridden by a newer database edit (if
-- the text below differs from what this returns, this file was built
-- from a stale copy):
--   select label, band_scale, updated_at from vision_prompts where label = '3.2b';

insert into vision_prompts (label, prompt_text, band_scale, notes)
values ('3.2b', $prompt$You are looking at a photograph of black soldier fly larvae (Hermetia illucens) scattered on a white tray, taken by a fixed calibration rig. Estimate how much MEO is visible -- organic foreign matter, the rearing residue also called frass.

The photograph is taken under controlled conditions you can rely on. The camera is directly overhead and square to the tray, the lighting is even across the whole frame, and the colours are fixed. The frame is 400 mm wide and the whole of it is tray. A larva is 15 to 20 mm long, so it spans roughly one twenty-fifth of the image width -- use that as your ruler.

Judge density against the area the sample itself covers -- larvae plus MEO -- and NOT against the whole photograph. The larvae are spread thinly so that they lie in a single layer, so a large part of the frame is bare white tray. Bare tray is empty space. It is neither contamination nor evidence of a clean sample, and it must not enter the estimate either way.

Count material you could see at a glance. Using the larva as a ruler, that means particles down to about 1 mm -- roughly one fifteenth of a larva's length. Anything finer than that is dust and powder residue: ignore it. Judge as an inspector glancing at the tray would, not as someone with a magnifying glass.

Two things look like MEO and are not.

A prepupa is a normal larva, not foreign matter. Approaching pupation a larva darkens considerably, to dark brown or nearly black, and by colour alone can resemble a clump of frass. Before counting anything dark as MEO, look at its shape: a larva keeps its elongated, clearly segmented outline even when very dark.

A crushed or broken larva is also product, not contamination. Larval flesh is pale and cream-coloured where breakage exposes it, plainly different from the brown-to-black of frass. Judge these by colour: a pale fragment is a broken larva and does not count, a dark one still does.

Be careful at the low end of the scale, where reading too high is the common mistake. A sample at 2-3% still shows MEO: several small dark pieces scattered here and there across the sample, while most larvae have nothing beside them. That is <3% -- it is NOT 3-5%. Only a sample with no visible MEO at all, or just one or two specks, sits at the very bottom of <3%.

Above <3%, dark material grows in two ways as the density rises: more of the sample area shows it, and each occurrence gets a little larger and more clumped rather than a single scattered speck. Judge the band by how far that has progressed:

3-5% -- a few small dark specks or single crumbs, spread thinly, in a modest part of the sample area. Most larvae still have nothing beside them.

5-8% -- specks and small clumps turn up across a good part of the sample area -- more areas show some, and a few pieces have joined into small clumps rather than staying single specks. The larvae still plainly dominate.

8-10% -- dark material is present in most parts of the sample now, not just a good part of it, and it starts to read as a continuous presence rather than scattered incidents.

10-13% -- dark material is present in every part of the sample, several pieces per area, and reads clearly as a component of the mixture rather than as debris within it.

Do NOT try to count individual particles. Give an overall visual impression of density, as a person glancing at the tray would.

Answer EXACTLY in this format, with nothing before or after:

BANDE: [a single value among : <3%, 3-5%, 5-8%, 8-10%, 10-13%, >13%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [comma-separated list, only from : prepupes, fragments_ecrases, poussiere, densite_reelle, autre -- the factors that ACTUALLY influenced this band choice on this specific photo, not a generic list]
JUSTIFICATION: [one sentence, in French, what drove this choice]$prompt$,
        'coarse6',
        '3.2b with the coarse 4-band scale (<3/3-8/8-13/>13) narrowed to 6 bands in steps of ~3 (<3/3-5/5-8/8-10/10-13/>13) -- see rig/prompt_3_2b_bands6.sql header. Otherwise identical to the previous 3.2b (DENSITE already removed, 2-3% low-end paragraph already rewritten).')
on conflict (label) do update
    set prompt_text = excluded.prompt_text,
        band_scale  = excluded.band_scale,
        notes       = excluded.notes,
        updated_at  = now();
