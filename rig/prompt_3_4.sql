-- Prompt 3.4 -- weight, not area; object identity over colour; bands described
-- by what is visible rather than by each other.
--
-- Built from a draft worked up in Copilot, tightened. Kept from 3.2b word for
-- word: the rig description, bare tray, the 1 mm floor, prepupae and crushed
-- larvae, and the answer format Caliban parses (minus DENSITE). Band labels and
-- scale are unchanged (coarse: <3%, 3-8%, 8-13%, >13%).
--
-- Why: on the 2026-09-12 operator samples every model read the 8%+ trays
-- (9.2% and 11.85% by lab) as 3-8%, estimates 5.5-6.75, while 3.2b's low-end
-- paragraph keeps clean trays down. The draft's key point -- MEO covers much
-- less of the picture than its share of the weight, so an 8-13% tray still
-- looks mostly like larvae -- is kept and said once. Its five separate
-- warnings against promoting to 8-13% are cut to one, because they push in the
-- direction of the error we actually have.
--
-- Also dropped: DENSITE. The density estimate tracked the lab poorly (r = 0.19)
-- and only costs tokens here; Caliban treats a missing DENSITE as no estimate.
--
-- The 8-13% description (clump size and total amount, not spread) is a working
-- hypothesis -- check it against the 10:00:06 and 10:35:26 photos of 2026-09-12
-- before trusting it.
--
-- Running this makes 3.4 selectable; it is not active until AQ picks it under
-- Gestion de l'échantillonnage. Test it on the Re-noter tab first, alongside
-- 3.2b, on the same operator samples.

insert into vision_prompts (label, prompt_text, band_scale, notes)
values ('3.4', $prompt$You are looking at a photograph of black soldier fly larvae (Hermetia illucens) scattered on a white tray, taken by a fixed calibration rig. Estimate how much MEO is present -- organic foreign matter, the rearing residue also called frass.

The photograph is taken under controlled conditions you can rely on. The camera is directly overhead and square to the tray, the lighting is even across the whole frame, and the colours are fixed. The frame is 400 mm wide and the whole of it is tray. A larva is 15 to 20 mm long, so it spans roughly one twenty-fifth of the image width -- use that as your ruler.

Judge against the area the sample itself covers -- larvae plus MEO -- and NOT against the whole photograph. The larvae are spread thinly so that they lie in a single layer, so a large part of the frame is bare white tray. Bare tray is empty space. It is neither contamination nor evidence of a clean sample, and it must not enter the estimate either way.

Count material you could see at a glance. Using the larva as a ruler, that means particles down to about 1 mm -- roughly one fifteenth of a larva's length. Anything finer than that is dust and powder residue: ignore it. Judge as an inspector glancing at the tray would, not as someone with a magnifying glass.

The bands are MEO percentage by WEIGHT, measured in the lab -- not the share of the picture that MEO covers. On these trays MEO covers much less of the picture than its share of the weight, so the larvae dominate the picture in almost every sample, including samples at 8-13%. A tray that looks mostly like clean larvae can still be 8% or more. Do not wait for frass to rival the larvae before choosing 8-13%; that only happens above 13%.

Count only distinct particles and clumps that are separate from the larvae. Ask of anything dark: would it still look like foreign material if the larvae around it were taken away? If not, it is not MEO. Colour alone is not evidence: segment banding, darker tail segments, shadows between larvae and dark areas where larvae overlap are all larva, and the overall darkness of the tray must never raise the estimate. When colour and shape disagree, trust shape -- a recognisable larva is product whatever its colour.

Two things look like MEO and are not.

A prepupa is a normal larva, not foreign matter. Approaching pupation a larva darkens considerably, to dark brown or nearly black, and by colour alone can resemble a clump of frass. Before counting anything dark as MEO, look at its shape: a larva keeps its elongated, clearly segmented outline even when very dark.

A crushed or broken larva is also product, not contamination. Larval flesh is pale and cream-coloured where breakage exposes it, plainly different from the brown-to-black of frass. Judge these by colour: a pale fragment is a broken larva and does not count, a dark one still does.

What each band looks like, judged on the occupied sample:

<3% -- Be careful here, where reading too high is the common mistake. A sample at 2-3% still shows MEO: several small dark pieces scattered here and there across the sample, while most larvae have nothing beside them. That is <3% -- it is NOT 3-8%. Only a sample with no visible MEO at all, or just one or two specks, sits at the very bottom of <3%.

3-8% -- Dark specks and small clumps sit beside a fair share of the larvae across most of the sample, but the pieces stay small: mostly specks and crumbs of a few millimetres, with only the occasional larger clump.

8-13% -- Dark material is present in every part of the sample, and there is clearly more of it in total than at 3-8%: clumps several millimetres across -- a quarter to a third of a larva's length -- are common rather than occasional. The larvae still plainly dominate the picture; that is normal at 8-13%. What separates it from 3-8% is the size and total amount of the clumps, not merely how many places frass turns up.

>13% -- Frass is abundant enough to compete with the larvae for attention, forming patches and loose mats rather than separate clumps.

When torn between two bands: between <3% and 3-8%, choose <3% unless dark material sits beside a fair share of the larvae; between 3-8% and 8-13%, choose 8-13% when sizeable clumps are common throughout, even though the larvae still dominate.

Do NOT try to count individual particles. Give an overall impression of how much MEO there is by weight, as an experienced inspector glancing at the tray would.

Answer EXACTLY in this format, with nothing before or after:

BANDE: [a single value among : <3%, 3-8%, 8-13%, >13%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [comma-separated list, only from : prepupes, fragments_ecrases, poussiere, densite_reelle, autre -- the factors that ACTUALLY influenced this band choice on this specific photo, not a generic list]
JUSTIFICATION: [one sentence, in French, what drove this choice]$prompt$,
        'coarse',
        'Weight not area, object identity over colour, bands described by what is visible, tie-break toward 8-13% when sizeable clumps are common. No DENSITE. From a Copilot draft.')
on conflict (label) do update
    set prompt_text = excluded.prompt_text,
        band_scale  = excluded.band_scale,
        notes       = excluded.notes,
        updated_at  = now();
