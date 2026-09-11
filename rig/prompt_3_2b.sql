-- Prompt 3.2b -- 3.2 with ONLY the low-end paragraph changed.
--
-- Everything else is 3.2 word for word, generated from the 3.2 text in
-- app/main.py rather than retyped, so any difference in results against 3.2
-- is that paragraph and nothing else. Band labels and scale are unchanged
-- (coarse: <3%, 3-8%, 8-13%, >13%), so nothing operations sees changes.
--
-- Why: truly low samples read 3-8%. 13 of 13 real values under 3% read high in
-- the 2026-09 data, and a 2.54% tray on 2026-09-11 read 3-8% on every rotation.
-- 3.2 describes <3% as "a few small isolated specks", which makes a real 2-3%
-- tray -- where MEO is plainly visible -- look like 3-8%. 3.2b describes what
-- a 2-3% tray actually looks like, and restores the explicit "NOT 3-8%" that
-- 3.0 had and 3.1 dropped.
--
-- Running this makes 3.2b selectable under Gestion de l'échantillonnage. It is
-- not active until AQ picks it there. It applies to QC and operator captures
-- alike, and each estimate records the prompt that produced it.
--
-- Check first that 3.2 is not overridden in the database (if this returns a
-- 3.2 row, its text is what is live, and 3.2b was built from the code copy):
--   select label, updated_at from vision_prompts where label = '3.2';

insert into vision_prompts (label, prompt_text, band_scale, notes)
values ('3.2b', $prompt$You are looking at a photograph of black soldier fly larvae (Hermetia illucens) scattered on a white tray, taken by a fixed calibration rig. Estimate how much MEO is visible -- organic foreign matter, the rearing residue also called frass.

The photograph is taken under controlled conditions you can rely on. The camera is directly overhead and square to the tray, the lighting is even across the whole frame, and the colours are fixed. The frame is 400 mm wide and the whole of it is tray. A larva is 15 to 20 mm long, so it spans roughly one twenty-fifth of the image width -- use that as your ruler.

Judge density against the area the sample itself covers -- larvae plus MEO -- and NOT against the whole photograph. The larvae are spread thinly so that they lie in a single layer, so a large part of the frame is bare white tray. Bare tray is empty space. It is neither contamination nor evidence of a clean sample, and it must not enter the estimate either way.

Count material you could see at a glance. Using the larva as a ruler, that means particles down to about 1 mm -- roughly one fifteenth of a larva's length. Anything finer than that is dust and powder residue: ignore it. Judge as an inspector glancing at the tray would, not as someone with a magnifying glass.

Two things look like MEO and are not.

A prepupa is a normal larva, not foreign matter. Approaching pupation a larva darkens considerably, to dark brown or nearly black, and by colour alone can resemble a clump of frass. Before counting anything dark as MEO, look at its shape: a larva keeps its elongated, clearly segmented outline even when very dark.

A crushed or broken larva is also product, not contamination. Larval flesh is pale and cream-coloured where breakage exposes it, plainly different from the brown-to-black of frass. Judge these by colour: a pale fragment is a broken larva and does not count, a dark one still does.

Be careful at the low end of the scale, where reading too high is the common mistake. A sample at 2-3% still shows MEO: several small dark pieces scattered here and there across the sample, while most larvae have nothing beside them. That is <3% -- it is NOT 3-8%. Only a sample with no visible MEO at all, or just one or two specks, sits at the very bottom of <3%. Reserve 3-8% for when foreign matter forms visible specks or clumps across a good part of the sample area -- dark material beside a fair share of the larvae, not just an occasional piece -- while the larvae still plainly dominate. At 8-13% dark material is a continuous presence rather than scattered incidents: there is some in every part of the sample, and it reads as a component of the mixture rather than as debris within it.

Do NOT try to count individual particles. Give an overall visual impression of density, as a person glancing at the tray would.

Answer EXACTLY in this format, with nothing before or after:

BANDE: [a single value among : <3%, 3-8%, 8-13%, >13%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [comma-separated list, only from : prepupes, fragments_ecrases, poussiere, densite_reelle, autre -- the factors that ACTUALLY influenced this band choice on this specific photo, not a generic list]
DENSITE: [the sample's bulk density in grams per litre. Anchor it on these two measured references from this same rig:
  - a tray of mostly large, puffy, well-rounded larvae is about 140 g/L
  - a tray of mostly small, flat, shrivelled larvae is about 250 g/L
Note the direction: bigger and puffier means LOWER density, because rounded larvae nest badly and trap air between them, while small flat ones pack closer together. Fine material fills the gaps and raises it further. Almost every sample falls between these two references. Do not answer outside 120-280 g/L unless the tray looks clearly more extreme than either description, and say so in the justification if you do. Give a single number or a narrow range.]
JUSTIFICATION: [one sentence, in French, what drove this choice]$prompt$,
        'coarse',
        '3.2 with only the <3% paragraph rewritten: describes a real 2-3% tray, restores "NOT 3-8%".')
on conflict (label) do update
    set prompt_text = excluded.prompt_text,
        band_scale  = excluded.band_scale,
        notes       = excluded.notes,
        updated_at  = now();
