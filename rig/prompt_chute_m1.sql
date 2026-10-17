-- Prompt chute-m1 -- chute-v1 asked to MEASURE before it bands.
--
-- CROPPED VIEW: the top 22% and left 10% of the photo are cut off before the
-- model sees it (vision_prompts.crop). Tim, 2026-10-17: the brown band across
-- the top is residue that builds up on the chute through the day (light at
-- 10:40, dense by 05:50 the next morning), and it sits where the larvae land
-- still bunched together; the left edge carries static specks and hardly any
-- product. Below the line the larvae have spread out.
--
-- Otherwise the same rules for what to ignore (shadows, chute marks, the
-- flash patch, empty chute) and the same description of frass as chute-v1.
-- What is new is the answer: before the band, the model reports
--
--   SCORE_MEO    an overall 0-100 score with fixed visual anchors -- finer
--                than the bands and not tied to weight, so it only has to
--                rank photos; the lab fit sets its scale
--   AIRE_MEO    frass as a percentage of the visible PRODUCT area (larvae +
--                MEO, never the photo area) -- a number, not a band
--   AMAS_PETITS  clumps by size class, each as aucun / peu / plusieurs /
--   AMAS_MOYENS  beaucoup with fixed count ranges, sized with the larva as
--   AMAS_GROS    the ruler
--   FINES        loose fine brown material (<1 mm) in and around the stream,
--                aucun / peu / moyen / beaucoup. chute-v1 told the model to
--                ignore anything under 1 mm as dust, and the inline readings
--                came out at about half the lab ME% on BB03/BB05 (2026-10-16).
--                The brown band at the top is build-up and is cropped away;
--                loose fines moving with the stream are measured separately
--                rather than ignored, so the lab fit can say whether they
--                carry weight.
--   FLUX         how thick the stream is (clairseme / moyen / dense), because
--                a thick stream hides more frass under larvae
--
-- Why: the lab measures WEIGHT. A count ignores size (one big clump outweighs
-- many crumbs); area is closer, and area by size class closer still, since a
-- particle's weight grows faster than its area. The band alone folds all of
-- that into one judgement that cannot be calibrated piece by piece. With the
-- measures recorded separately, each can be fitted against lot lab ME% and the
-- best predictor kept -- which may or may not turn out to be the band.
--
-- The measures come BEFORE the band in the answer on purpose: the model writes
-- down what it sees, then bands it. It also means a reply cut short by the
-- token limit loses the justification, not the numbers.
--
-- For re-scoring stored inline photos only, next to chute-v1 on the same
-- photos (the "Photos en ligne d'une journée" source on the re-score page of
-- validation-band-test.html). It is NOT for inline_settings: the live rig keeps
-- chute-v1 until this has been compared against lab values. Needs the Caliban
-- that parses these lines (parse_band_response / _attach_measures) and
-- rig/rescore_mesures.sql for the re-score page to keep them.
--
-- UNCALIBRATED, like chute-v1. English instructions, French output field names
-- and values (CLAUDE.md). Same coarse6 band scale, so its band and estimate_pct
-- compare directly with chute-v1's.

-- The crop: the share of the photo cut off each side before the model sees it.
-- The stored photo stays whole. Caliban (prompt_crop) applies it to every
-- reading and re-score made with this label, and folds it into prompt_hash, so
-- moving the line later is traceable like a text change.
alter table vision_prompts add column if not exists crop jsonb;

insert into vision_prompts (label, prompt_text, band_scale, crop, notes)
values ('chute-m1', $prompt$You are looking at a flash photograph taken by a fixed camera mounted directly above an inclined grey steel chute, just after the destoner on a black soldier fly (Hermetia illucens) larvae production line. Larvae fall through the frame in a stream, together with whatever organic foreign matter (MEO) is mixed in with them -- the dried rearing residue also called frass. Your job is to MEASURE the MEO in the falling product in a few specific ways, then estimate its band.

How the photograph is made, so you know what you are looking at. The flash freezes the falling product, so every larva and particle in the stream is sharp. The frame is about 20 cm wide; a larva is 15 to 20 mm long, roughly one tenth to one thirteenth of the image width -- use that as your ruler. The photograph has been cropped to the part of the chute below where the product lands, where the larvae have spread out. The colours are fixed and reliable.

Judge ONLY the product falling through the frame: the larvae and the loose particles travelling with them. Everything else in the photograph is the chute and must be ignored completely:

- Shadows. Each falling larva and particle casts a shadow onto the chute below it. Shadows are dark grey to black, soft-edged, flat, and shaped like the object that casts them -- most are larva-shaped -- and they all lie offset in the same direction from their objects. They have no texture and no colour of their own. A shadow is NEVER MEO, however dark.
- The chute surface. It is matte grey steel with permanent marks: small white specks, hairline cracks, darker stains and worn patches. These marks are part of the chute and look the same in every photograph. Loose fine brown particles travelling with the stream -- a haze or spray of brown specks among or around the larvae -- are different and are NOT to be ignored. Do not decide whether they are frass; report how much there is in measurement 3 below, and leave them out of the clump measurements.
- The bright patch. The flash reflects off the chute as a bright, washed-out area, usually in the lower centre of the frame. Ignore it, and do not read the product inside it as paler or cleaner than elsewhere.
- Empty chute. The stream usually falls in two bands, left and right, with a thinner gap between them, and how much product passes changes from one moment to the next. Empty chute is never part of any measurement below.

What MEO looks like in the stream. MEO here is NOT mainly black or dark. It is dried residue: separate, matte, porous, crumbly lumps with irregular, ragged outlines, like crumbs of dried soil or bark. Its colour is grey-brown to greyish-tan, duller and greyer than the larvae, sometimes flecked with darker specks. It has no gloss, no segments and no elongated tapered shape. Material finer than about 1 mm is not a clump; it is reported separately as fines (measurement 3).

What is NOT MEO:

Larvae are glossy, elongated, tapered at both ends and clearly segmented, golden-tan with darker brown bands. Segment bands, darker tail ends and dark areas where falling larvae overlap are all larva.

Prepupae are larvae approaching pupation: the same elongated, segmented shape, but dark brown to nearly black. They are product, and usually the darkest objects in the stream. A dark colour is therefore NOT a sign of MEO -- judge by shape and texture, never by darkness alone.

Broken or crushed larvae are product too: their exposed flesh is smooth, glossy and cream to yellow, and torn skin still shows segment lines. Frass is matte and crumbly; larval pieces are glossy and smooth.

THE MEASUREMENTS. Make each one on its own, from the photograph, before thinking about the band.

0. Overall MEO score. On a scale of 0 to 100, how much MEO is in the falling product, as an experienced inspector would rate it at a glance? Use these anchors:
   0 = no visible MEO at all, only larvae and prepupae;
   10 = a few small crumbs or one isolated clump;
   25 = several separate clumps scattered through the stream;
   50 = clumps in every part of the stream, many of them medium-sized;
   75 = clumps frequent and sizeable everywhere, falling together in loose groups;
   100 = MEO competing with the larvae for attention, falling as patches and mats.
   Any whole number in between is allowed and expected -- use the full scale, not just the anchors, and place this photograph relative to them.

1. MEO area. Consider only the area of the photograph covered by product -- larvae plus MEO, not the chute, not the shadows. Of that product area, what percentage is MEO? Answer with a whole number. On this line the honest answer is usually small: a clean stream is 0 to 2, and even a stream with clumps everywhere is rarely above 15. Do not round to 5 or 10 unless that is genuinely what you see.

2. Clumps by size. Using the larva as your ruler, sort the MEO clumps you can see into three sizes:
   - small: about 1 to 3 mm, up to a fifth of a larva long -- crumbs;
   - medium: about 3 to 10 mm, a fifth to half a larva long;
   - large: over about 10 mm, more than half a larva long.
   For each size, say how many there are in the whole photograph, using exactly one of these words:
   aucun = none; peu = 1 to 5; plusieurs = 6 to 20; beaucoup = more than 20.
   Estimate at a glance; do not count one by one. A clump hidden behind a larva is not counted.

3. Fines. How much loose fine brown material -- specks and powder finer than about 1 mm, as a haze or spray among or around the falling product -- is in the photograph?
   aucun = none visible; peu = a light scattering of specks; moyen = a clearly visible haze over part of the stream; beaucoup = a dense brown haze or spray over a large part of the frame.

4. Stream. How much of the frame does the falling product cover?
   clairseme = scattered larvae with much bare chute between them; moyen = a continuous stream with some gaps; dense = larvae packed and overlapping, little chute showing.

THE BAND. The bands are MEO percentage by WEIGHT, measured in the lab on the bagged product. MEO shows much less in a photograph than its share of the weight, so even a stream at 8-13% still looks mostly like larvae. At the low end the opposite mistake is the common one: a few clumps you can point to are still <3%. Fine brown material moving with the stream (measurement 3) counts toward the band as well as the clumps: the lab weighs it too. Judge on the product in the stream:

<3% -- Only larvae, often with some dark prepupae, plus at most a few small crumbs and one or two larger clumps. A single big clump on an otherwise clean stream is still <3%.
3-5% -- Several separate clumps scattered through the stream, each an isolated incident with clean stretches of larvae between them.
5-8% -- Clumps turn up throughout the stream, including several medium clumps; few parts of the stream are entirely free of them.
8-10% -- Crumbly grey-brown clumps in every part of the stream, several near almost any group of larvae, many of them medium. The larvae still dominate the picture; that is normal here.
10-13% -- Clumps are frequent and sizeable everywhere, some falling together in loose groups, though larvae are still the majority.
>13% -- Clumps are abundant enough to compete with the larvae for attention, falling as patches and loose mats rather than separate lumps.

Answer EXACTLY in this format, with nothing before or after, in this order:

SCORE_MEO: [a whole number from 0 to 100, the overall MEO score]
AIRE_MEO: [a whole number from 0 to 100, the MEO percentage of the product area]
AMAS_PETITS: [aucun, peu, plusieurs, or beaucoup]
AMAS_MOYENS: [aucun, peu, plusieurs, or beaucoup]
AMAS_GROS: [aucun, peu, plusieurs, or beaucoup]
FINES: [aucun, peu, moyen, or beaucoup]
FLUX: [clairseme, moyen, or dense]
BANDE: [a single value among : <3%, 3-5%, 5-8%, 8-10%, 10-13%, >13%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [comma-separated list, only from : prepupes, fragments_ecrases, ombres, fines, flux_clairseme, autre -- the factors that ACTUALLY influenced this band choice on this specific photo, not a generic list]
JUSTIFICATION: [one sentence, in French, what drove this choice]$prompt$,
        'coarse6',
        '{"top": 0.22, "left": 0.10}',
        'Cropped (top 22%, left 10%). chute-v1 asked to measure before it bands: 0-100 anchored MEO score, fines measured not ignored, MEO share of product area (number), clump counts by size class (aucun/peu/plusieurs/beaucoup), stream density, then the band. For offline re-scoring against chute-v1; not live. Uncalibrated.')
on conflict (label) do update
    set prompt_text = excluded.prompt_text,
        band_scale  = excluded.band_scale,
        crop        = excluded.crop,
        notes       = excluded.notes,
        updated_at  = now();
