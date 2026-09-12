-- Prompt 3.4b -- MEO described as it actually looks on this line, bands written
-- from real operator trays with lab values.
--
-- Why: looking at operator photos with lab ME% (2026-09-12 10:00:06 at 9.2%,
-- 10:35:26 at 11.85%, 11:43:01 at 6.7%; 2026-09-11 13:44:07 clean) the frass is
-- NOT dark. It is matte, porous, crumbly grey-brown to greyish-tan lumps, often
-- duller and greyer than the larvae; the darkest objects on the tray are the
-- prepupae, which are product. 3.2b and 3.4 describe MEO as "dark pieces" and
-- tell the model a pale fragment is a broken larva, so the model looks for dark
-- specks, finds few, and discounts the grey clumps -- every 8%+ tray read 3-8%.
-- 3.4b separates frass from larvae by texture (matte and crumbly vs glossy and
-- segmented), says to look into the gaps of packed areas where clumps hide, and
-- describes each band by how often and how large the clumps are on those trays.
-- The weight-not-area point from 3.4 is kept but limited to the 8% boundary,
-- because as a general rule it lifted the clean trays too.
--
-- Answer format as 3.4 (no DENSITE). Band labels and scale unchanged (coarse).
-- Not active until AQ picks it; test on the Re-noter tab against 3.2b and 3.4,
-- leaving out the samples taken before the rig light was on. Its references
-- trays (the four above) are in the wording, so judge it on other samples too.

insert into vision_prompts (label, prompt_text, band_scale, notes)
values ('3.4b', $prompt$You are looking at a photograph of black soldier fly larvae (Hermetia illucens) on a white tray, taken by a fixed calibration rig. Estimate how much MEO is present -- organic foreign matter, the dried rearing residue also called frass.

The photograph is taken under controlled conditions you can rely on. The camera is directly overhead and square to the tray, and the colours are fixed. The frame is 400 mm wide. A larva is 15 to 20 mm long, so it spans roughly one twenty-fifth of the image width -- use that as your ruler. A band of soft striped shadow may appear along one edge of the frame, beyond the sample: it is part of the rig, not the sample, and must be ignored.

Judge against the area the sample itself covers -- larvae plus MEO -- and NOT against the whole photograph. Bare white tray is empty space: it is neither contamination nor evidence of a clean sample, and it must not enter the estimate either way. The larvae lie mostly in one layer but are packed tightly in places.

Count material you could see at a glance. Using the larva as a ruler, that means particles down to about 1 mm -- roughly one fifteenth of a larva's length. Anything finer than that is dust: ignore it. Judge as an inspector glancing at the tray would, not as someone with a magnifying glass.

What MEO looks like on this line -- read this carefully, because it is not what you might expect. MEO here is NOT mainly black or dark. It is dried residue: matte, porous, crumbly lumps with irregular, ragged outlines, like crumbs of dried soil or bark. Its colour is grey-brown to greyish-tan, often duller and greyer than the larvae, sometimes flecked with darker specks. It has no gloss, no segments and no elongated shape. Pieces range from crumbs of about 1 mm to clumps half a larva long. In open parts of the tray they are easy to see; in packed areas they sit wedged in the gaps between larvae, so look into those gaps and not only at the bare tray.

What is NOT MEO:

Larvae are glossy, elongated, tapered at both ends and clearly segmented, golden-tan with darker brown bands. Segment bands, darker tail ends, shadows between larvae and dark areas where larvae overlap are all larva.

Prepupae are larvae approaching pupation: the same elongated, segmented shape, but dark brown to nearly black. They are product, and they are usually the darkest objects on the tray. A dark colour is therefore NOT a sign of MEO -- judge by shape and texture.

Broken or crushed larvae are product too. Their exposed flesh is smooth, glossy and cream to yellow, and the torn skin still shows segment lines. Frass is matte and crumbly; larval pieces are glossy and smooth. When in doubt, judge by texture, not colour.

The bands are MEO percentage by WEIGHT, measured in the lab. Around the 8% boundary this matters: MEO covers much less of the picture than its share of the weight, so an 8-13% tray still looks mostly like larvae -- do not wait for frass to rival the larvae before choosing 8-13%. At the low end the opposite mistake is the common one, and the <3% description below takes precedence.

What each band looks like, judged on the occupied sample:

<3% -- Only larvae, perhaps with a few dark prepupae, and no crumbly clumps -- or just a few small crumbs, scattered here and there, while most of the tray has none. A sample at 2-3% still shows a few crumbs: that is <3%, it is NOT 3-8%.

3-8% -- Crumbly clumps turn up across the tray but stay occasional: a handful of small crumbs in most areas and only one or two clumps as large as a third of a larva. Many parts of the tray show no clump at all.

8-13% -- Crumbly grey-brown clumps are found in every part of the tray, several in each area: dozens across the whole sample, many of them 3 to 10 mm -- a quarter to half a larva long -- both in the open patches and wedged among the larvae. The larvae still dominate the picture; that is normal at 8-13%.

>13% -- Clumps are abundant enough to compete with the larvae for attention, forming patches and loose mats rather than separate lumps.

When torn between two bands: between <3% and 3-8%, choose <3% unless crumbly clumps appear in most areas of the tray; between 3-8% and 8-13%, choose 8-13% when clumps are found in every area and many of them are sizeable, even though the larvae still dominate.

Do NOT try to count individual particles. Give an overall impression of how much MEO there is by weight, as an experienced inspector glancing at the tray would.

Answer EXACTLY in this format, with nothing before or after:

BANDE: [a single value among : <3%, 3-8%, 8-13%, >13%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [comma-separated list, only from : prepupes, fragments_ecrases, poussiere, densite_reelle, autre -- the factors that ACTUALLY influenced this band choice on this specific photo, not a generic list]
JUSTIFICATION: [one sentence, in French, what drove this choice]$prompt$,
        'coarse',
        'MEO described as matte crumbly grey-brown lumps (not dark); prepupae named as the dark objects; texture over colour; bands from real trays; weight point limited to the 8% boundary. No DENSITE.')
on conflict (label) do update
    set prompt_text = excluded.prompt_text,
        band_scale  = excluded.band_scale,
        notes       = excluded.notes,
        updated_at  = now();
