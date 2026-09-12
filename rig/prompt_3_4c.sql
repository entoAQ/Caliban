-- Prompt 3.4c -- 3.4b with ONLY the low end rewritten (<3%, 3-8%, and the
-- <3/3-8 tie-break). Generated from prompt_3_4b.sql so nothing else differs.
--
-- Why: 3.4b read all five 8%+ trays of 2026-09-12 as 8-13% (estimates 8.0-10.5)
-- but also read all five clean trays (0.6-2.2% by lab) as 3-8%. The clean trays
-- show why: a 2.2% tray (09:53:15) has a handful of crumbly lumps you can point
-- to one by one, which is exactly what 3.4b called 3-8%; a 1.9% tray (12:59:30)
-- has one larva-sized lump and is otherwise clean; a 0.6% tray (13:32:00) has
-- only pale papery flakes. The 6.7% tray (11:43:01) has several medium clumps
-- over much of the tray. 3.4c draws the <3 / 3-8 line there: countable
-- individually versus spread and too many to point to.
--
-- CAUTION: most of the 2026-09-12 trays are now references for this wording
-- (clean 09:53:15, 12:59:30, 13:32:00; 6.7% 11:43:01; 8%+ 10:00:06, 10:35:26),
-- so today's set will flatter it. The honest test is the next day's samples.
--
-- Answer format as 3.4b (no DENSITE). Coarse scale unchanged. Not active until
-- AQ picks it under Gestion de l'échantillonnage.

insert into vision_prompts (label, prompt_text, band_scale, notes)
values ('3.4c', $prompt$You are looking at a photograph of black soldier fly larvae (Hermetia illucens) on a white tray, taken by a fixed calibration rig. Estimate how much MEO is present -- organic foreign matter, the dried rearing residue also called frass.

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

<3% -- Few enough clumps that you could point to each one: only larvae, often with some dark prepupae, plus a handful of small crumbs and at most one or two larger clumps across the whole tray. A single big clump -- even one as large as a larva -- on an otherwise clean tray is still <3%: one lump weighs little against the whole sample. Thin, pale, papery flakes, lighter in colour than the larvae, weigh almost nothing and do not raise the band. A tray at 2-3% does show some MEO: that is still <3%, it is NOT 3-8%.

3-8% -- Too many clumps to point to one by one: grey crumbly lumps turn up across most of the tray, including several medium clumps a quarter to a third of a larva long, yet they remain separate incidents and parts of the tray are still clean.

8-13% -- Crumbly grey-brown clumps are found in every part of the tray, several in each area: dozens across the whole sample, many of them 3 to 10 mm -- a quarter to half a larva long -- both in the open patches and wedged among the larvae. The larvae still dominate the picture; that is normal at 8-13%.

>13% -- Clumps are abundant enough to compete with the larvae for attention, forming patches and loose mats rather than separate lumps.

When torn between two bands: between <3% and 3-8%, choose <3% if you could point to each clump individually, and 3-8% only when clumps -- several of them medium-sized -- are spread over most of the tray; between 3-8% and 8-13%, choose 8-13% when clumps are found in every area and many of them are sizeable, even though the larvae still dominate.

Do NOT try to count individual particles. Give an overall impression of how much MEO there is by weight, as an experienced inspector glancing at the tray would.

Answer EXACTLY in this format, with nothing before or after:

BANDE: [a single value among : <3%, 3-8%, 8-13%, >13%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [comma-separated list, only from : prepupes, fragments_ecrases, poussiere, densite_reelle, autre -- the factors that ACTUALLY influenced this band choice on this specific photo, not a generic list]
JUSTIFICATION: [one sentence, in French, what drove this choice]$prompt$,
        'coarse',
        '3.4b with the low end rewritten: <3% = clumps you could point to one by one (single big lump or pale flakes do not raise the band); 3-8% = too many to point to, several medium, spread over most of the tray.')
on conflict (label) do update
    set prompt_text = excluded.prompt_text,
        band_scale  = excluded.band_scale,
        notes       = excluded.notes,
        updated_at  = now();
