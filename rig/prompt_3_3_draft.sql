-- Prompt 3.3 -- DRAFT for review.
--
-- Running this makes 3.3 selectable under Gestion de l'échantillonnage. It is
-- NOT active until AQ picks it there, and it should not be picked until it has
-- been compared with 3.2 by re-scoring stored captures.
--
-- Built from 3.1 (coarse bands). Each change answers a finding from the
-- 2026-09 data rather than a matter of style:
--
-- 1. Bands rewritten around the 8% line. The destoner instruction turns on at
--    8%, and that is exactly where the model is weakest: the two highest real
--    samples (7.49%, 7.55%) both read about two points low. 3-8% and 8-13% now
--    differ by one checkable feature -- is any sizeable area of the sample free
--    of MEO? -- instead of by adjectives ("scattered", "continuous") the model
--    can slide between.
--
-- 2. Explicit negatives restored ("This is not 3-8%."). 3.0 said "not 3-7%" at
--    the low end; the 3.1 rewrite dropped it, and 13 of 13 clean samples then
--    read high. Now that <3% triggers "decrease", that matters again.
--
-- 3. Larva-relative ruler. 3.0 measured by "the frame is 400 mm wide", which is
--    true only while the rig geometry is unchanged -- and moving the rig to the
--    line is exactly the kind of change that alters it. A larva is 15-20 mm on
--    any rig.
--
-- 4. Colour-relative crushed-larva rule. "Pale" and "brown-to-black" are
--    absolute colours and drift with white balance; "lighter or darker than the
--    intact larvae beside it" does not.
--
-- 5. DENSITE removed. It correlated with measured density at r = 0.19 and
--    tracked the band rather than morphology. It cost output tokens and a
--    second judgement made ahead of the one that matters.
--
-- The output lines are otherwise unchanged, so parse_band_response() needs
-- nothing, and 'coarse' keeps the labels and midpoints 3.1/3.2 use.

insert into vision_prompts (label, prompt_text, band_scale, notes)
values ('3.3', $prompt$You are looking at a photograph of black soldier fly larvae (Hermetia illucens) spread on a white tray, taken from directly overhead by a fixed rig under even lighting. Estimate how much MEO is visible -- organic foreign matter, the rearing residue also called frass.

Use the larvae as your ruler. A larva is 15 to 20 mm long. Judge the size of every particle against the larvae lying beside it, never against the size of the image.

Judge density against the area the sample itself covers -- larvae plus MEO -- and NOT against the whole photograph. The larvae are spread in a single layer, so part of the frame is bare white tray. Bare tray is empty space. It is neither contamination nor evidence of a clean sample, and it must not enter the estimate either way.

Count material you could see at a glance: particles down to about one fifteenth of a larva's length. Anything finer is dust and powder residue -- ignore it. Judge as an inspector glancing at the tray would, not as someone with a magnifying glass.

Two things look like MEO and are not.

A prepupa is a normal larva, not foreign matter. Approaching pupation a larva darkens considerably, to dark brown or nearly black, and by colour alone can resemble a clump of frass. Before counting anything dark as MEO, look at its shape: a larva keeps its elongated, clearly segmented outline even when very dark.

A crushed or broken larva is product, not contamination. Where breakage exposes larval flesh it is LIGHTER than the intact larvae around it; frass is DARKER than them. Compare each fragment with the larvae beside it: lighter does not count, darker does.

Choose the band from how the foreign matter is distributed across the sample:

<3% -- MEO is occasional. A few isolated specks, well apart, and most of the sample shows none at all. This is not 3-8%.

3-8% -- MEO is present but patchy. Specks or small clumps in several places, with clearly clean larvae between them. Dark material reads as debris among the larvae, not as part of the mix. This is not 8-13%.

8-13% -- MEO is present everywhere. There is dark material in every part of the sample, not just in patches, and it begins to read as a component of the mixture. The larvae still dominate, but no area of the sample looks clean.

>13% -- MEO is a major component. Dark material is continuous and in places competes with the larvae for area.

The line that matters most is the one between 3-8% and 8-13%. When a sample is near it, ask one question: is there any sizeable area of the sample -- several larvae across -- with no visible MEO? If yes, it is 3-8%. If dark material is everywhere, it is 8-13%.

Do NOT try to count individual particles. Give an overall visual impression of density, as a person glancing at the tray would.

Answer EXACTLY in this format, with nothing before or after:

BANDE: [a single value among : <3%, 3-8%, 8-13%, >13%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [comma-separated list, only from : prepupes, fragments_ecrases, poussiere, densite_reelle, autre -- the factors that ACTUALLY influenced this band choice on this specific photo, not a generic list]
JUSTIFICATION: [one sentence, in French, what drove this choice]$prompt$,
        'coarse',
        'DRAFT: bands built around the 8% line, negatives restored, larva-relative ruler, no density.')
on conflict (label) do update
    set prompt_text = excluded.prompt_text,
        band_scale  = excluded.band_scale,
        notes       = excluded.notes,
        updated_at  = now();
