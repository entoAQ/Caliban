-- Prompt 3.4b -- band scale narrowed from 4 bands to 6, in steps of
-- roughly 3 (coarse -> coarse6 in app/main.py's BAND_SCALES). Same change
-- as rig/prompt_3_2b_bands6.sql applied to 3.4b: only the BANDE list and
-- the per-band descriptions change; the MEO-by-texture wording, the
-- weight-vs-area paragraph and everything else is the current 3.4b text
-- word for word.
--
-- Why: see prompt_3_2b_bands6.sql -- AQ asked for the same 6-band
-- resolution (<3%, 3-5%, 5-8%, 8-10%, 10-13%, >13%) on both operator
-- prompts, so the band shown always matches whichever of the two decided
-- (low_variant vs high_variant), and OP_DECREASE_MAX / OP_INCREASE_MIN
-- keep meaning the same thresholds (3.0 / 8.0) either way.
--
-- The two existing per-band paragraphs here (3-8%, 8-13%) were written
-- from real operator trays against lab ME% (see the original prompt_3_4b.sql
-- header: 2026-09-12 readings at 9.2%, 11.85%, 6.7%, plus a clean 2026-09-11
-- tray). Splitting each into two keeps every original clump-density
-- description intact as the boundary text for its half and interpolates
-- the new in-between band -- it does NOT add new reference trays, since
-- none exist yet at exactly 4-5%, 6-7% or 9-10%. Treat the new bands as a
-- reasoned interpolation until real trays at those levels confirm or
-- correct them, the same caution 3.4b already carries for its 8% weight
-- point.
--
-- Editing in place, not a new label -- same db-sourced convention as
-- 3.2b (see that file's header for the fuller reasoning).
--
-- band_scale changes from 'coarse' to 'coarse6' (app/main.py).
--
-- Check first that 3.4b is not overridden by a newer database edit:
--   select label, band_scale, updated_at from vision_prompts where label = '3.4b';

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

The bands are MEO percentage by WEIGHT, measured in the lab. Around the 8% boundary this matters: MEO covers much less of the picture than its share of the weight, so an 8-10% tray still looks mostly like larvae -- do not wait for frass to rival the larvae before choosing 8-10% or 10-13%. At the low end the opposite mistake is the common one, and the <3% description below takes precedence.

What each band looks like, judged on the occupied sample:

<3% -- Only larvae, perhaps with a few dark prepupae, and no crumbly clumps -- or just a few small crumbs, scattered here and there, while most of the tray has none. A sample at 2-3% still shows a few crumbs: that is <3%, it is NOT 3-5%.

3-5% -- Crumbly clumps start to turn up beyond isolated crumbs, but stay sparse: a small crumb here and there across a modest part of the tray, with most areas still showing none.

5-8% -- Crumbly clumps are found across a good part of the tray, occasional but no longer rare: a handful of small crumbs in most areas and one or two clumps as large as a third of a larva. Some parts of the tray still show no clump at all.

8-10% -- Crumbly grey-brown clumps are found in most parts of the tray, several in each area, several millimetres across -- more consistent than 5-8% but not yet in literally every area. The larvae still clearly dominate.

10-13% -- Crumbly grey-brown clumps are found in every part of the tray, several in each area: dozens across the whole sample, many of them 3 to 10 mm -- a quarter to half a larva long -- both in the open patches and wedged among the larvae. The larvae still dominate the picture; that is normal at 10-13%.

>13% -- Clumps are abundant enough to compete with the larvae for attention, forming patches and loose mats rather than separate lumps.

When torn between two bands: between <3% and 3-5%, choose <3% unless more than a couple of crumbs appear outside the immediate area of one; between 5-8% and 8-10%, choose 8-10% once most areas of the tray show at least one clump, even if a few still show none; between 8-10% and 10-13%, choose 10-13% only once every area shows clumps, not just most.

Do NOT try to count individual particles. Give an overall impression of how much MEO there is by weight, as an experienced inspector glancing at the tray would.

Answer EXACTLY in this format, with nothing before or after:

BANDE: [a single value among : <3%, 3-5%, 5-8%, 8-10%, 10-13%, >13%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [comma-separated list, only from : prepupes, fragments_ecrases, poussiere, densite_reelle, autre -- the factors that ACTUALLY influenced this band choice on this specific photo, not a generic list]
JUSTIFICATION: [one sentence, in French, what drove this choice]$prompt$,
        'coarse6',
        '3.4b with the coarse 4-band scale narrowed to 6 bands in steps of ~3 (<3/3-5/5-8/8-10/10-13/>13) -- see rig/prompt_3_4b_bands6.sql header. The two original per-band descriptions (3-8%, 8-13%, from real 2026-09-11/12 trays) were each split into two rather than retyped from scratch; the new in-between bands (3-5%/8-10%) are interpolated, not yet confirmed against reference trays at those exact levels. Otherwise identical to the previous 3.4b.')
on conflict (label) do update
    set prompt_text = excluded.prompt_text,
        band_scale  = excluded.band_scale,
        notes       = excluded.notes,
        updated_at  = now();
