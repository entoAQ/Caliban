-- Prompt chute-g2 -- chute-g1 with larva size and flow density taken out of the judgement.
--
-- Why (2026-10-17): live g1 on 20261017 read BB08 ~11% and BB09 ~8.8% (calibrated)
-- while both looked clean; the afternoon flow was heavier, with more small larvae.
-- Two faults in g1's wording push exactly that case up:
--   1. Larvae are described only as 15-20 mm. A young larva, or one falling
--      end-on or half hidden in a dense stream, is a short rounded 3-8 mm blob --
--      by g1's own size rules a small/medium clump. g2 says these are product.
--   2. g1's bands and score are absolute ("clumps in every photograph"), so a
--      heavier stream -- more product in the same four photos -- shows more clumps
--      at the same % and reads dirtier. g2 judges clumps RELATIVE to the amount of
--      larvae (a share, like the lab), and stops capping the larva count.
-- Otherwise as g1 (crop, counts, output). Re-score against g1 on the 10-16 lots
-- and 20261017-BB07..09 before any live use. UNCALIBRATED: g1's line does not carry over.
-- English instructions, French output fields (CLAUDE.md).

alter table vision_prompts add column if not exists crop jsonb;

insert into vision_prompts (label, prompt_text, band_scale, crop, notes)
values ('chute-g2', $prompt$You are given several flash photographs (usually four), numbered Photo 1, Photo 2 and so on, taken one after another by a fixed camera above an inclined grey steel chute just after the destoner on a black soldier fly (Hermetia illucens) larvae production line. Each shows a different moment of the same falling stream of product. TOGETHER they are one sample of the product, about the size of a tray sample: judge them as one sample, not one by one. Larvae fall through each frame together with whatever organic foreign matter (MEO) is mixed in with them -- the dried rearing residue also called frass.

How the photographs are made. The flash freezes the falling product, so every larva and particle is sharp. Each photograph has been cropped to the part of the chute below where the product lands; it is about 20 cm wide, and a larva (15 to 20 mm) is roughly one tenth to one thirteenth of its width -- use that as your ruler. The colours are fixed and reliable.

Judge ONLY the falling product: the larvae and the loose particles travelling with them. Ignore completely:
- Shadows. Each falling larva and particle casts a dark, soft-edged, flat shadow onto the chute, offset in the same direction for every object, shaped like what casts it. A shadow is NEVER MEO.
- The chute surface: matte grey steel with permanent white specks, hairline cracks, stains and worn patches.
- The bright patch where the flash reflects off the chute, usually in the lower centre. Do not read the product inside it as paler or cleaner.
- Empty chute between and beside the streams. It is neither clean product nor contamination.

What MEO looks like. MEO is NOT mainly black or dark. It is dried residue: separate, matte, porous, crumbly lumps with irregular, ragged outlines, like crumbs of dried soil or bark, grey-brown to greyish-tan, duller and greyer than the larvae, sometimes flecked darker. No gloss, no segments, no elongated tapered shape.

What is NOT MEO. Larvae: glossy, elongated, tapered at both ends, clearly segmented, golden-tan with darker bands. Prepupae: the same shape, dark brown to nearly black -- product, and usually the darkest objects; never judge MEO by darkness. Broken or crushed larvae: smooth, glossy, cream to yellow flesh, torn skin with segment lines -- product.
Larvae are NOT all the same size. Young larvae can be 5 to 10 mm: same gloss, same segments, same golden-tan colour, just shorter -- product, never a clump. A larva falling end-on, tilted towards the camera, or half hidden behind others shows as a short rounded glossy blob -- also product. Before calling anything a clump, check it is matte, porous and ragged; if it has any gloss, smooth outline or segment lines, it is a larva.

THE COUNTS, over ALL the photographs together. Estimate; do not count one by one, but give real numbers, not words:
- larvae and prepupae, to the nearest 50 -- in a dense stream this can be well over 1000; give the real estimate, do not stop at a round ceiling;
- MEO clumps by size, using the larva as your ruler: small = about 1 to 3 mm (up to a fifth of a larva); medium = about 3 to 10 mm (a fifth to half a larva); large = over about 10 mm (more than half a larva). A clump hidden behind a larva is not counted; anything under about 1 mm is not a clump.
Write each count as digits only, with no spaces or separators (1200, not 1 200).

HOW MUCH PRODUCT. The stream is sometimes thin and sometimes heavy. A heavier stream puts more larvae AND proportionally more clumps into the same photographs at the same contamination. Contamination is a SHARE: judge the clumps against the amount of larvae they travel with, never as a raw number. Twice the larvae with twice the clumps is the SAME contamination.

THE SCORE. On a scale of 0 to 100, how much MEO is in the whole sample RELATIVE to the larvae, as an experienced inspector would rate it? Anchors: 0 = no visible MEO; 10 = a few small crumbs among many larvae; 25 = a clump among every few dozen larvae; 50 = clumps beside almost every small group of larvae, many medium-sized; 75 = clumps frequent and sizeable everywhere; 100 = MEO competing with the larvae for attention. Use any whole number in between.

THE BAND is MEO percentage by WEIGHT, measured in the lab on the bagged product, judged as a share of the product: the clumps relative to the larvae they fall with, whatever the stream density. MEO shows much less in a photograph than its share of the weight, so even 8-13% still looks mostly like larvae.

<3% -- Clumps rare for the amount of larvae: small crumbs here and there, the odd larger clump, most of the stream clean.
3-5% -- A small clump among every few dozen larvae, mostly small, the odd medium one.
5-8% -- Clumps regularly among the larvae, including several medium ones across the sample.
8-10% -- Clumps near almost any group of larvae, many of them medium. The larvae still dominate; that is normal.
10-13% -- Clumps frequent and sizeable everywhere, some falling together in loose groups.
>13% -- Clumps abundant enough to compete with the larvae for attention, falling as patches and loose mats.

Decide by how widespread the clumps are relative to the larvae across the whole sample -- not by how busy the photographs are, not by any single photograph or any single large clump.

Answer EXACTLY in this format, with nothing before or after, in this order:

NB_LARVES: [whole number, digits only]
NB_PETITS: [whole number, digits only]
NB_MOYENS: [whole number, digits only]
NB_GROS: [whole number, digits only]
SCORE_MEO: [a whole number from 0 to 100]
BANDE: [a single value among : <3%, 3-5%, 5-8%, 8-10%, 10-13%, >13%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [comma-separated list, only from : prepupes, fragments_ecrases, ombres, flux_clairseme, autre -- the factors that ACTUALLY influenced this band choice, not a generic list]
JUSTIFICATION: [one sentence, in French, what drove this choice]$prompt$,
        'coarse6',
        '{"top": 0.22, "left": 0.10}',
        'chute-g1 with small/end-on larvae declared product and clumps judged relative to the larvae (flow-density independent); larva count uncapped. Re-score only until compared with g1. Uncalibrated.')
on conflict (label) do update
    set prompt_text = excluded.prompt_text,
        band_scale  = excluded.band_scale,
        crop        = excluded.crop,
        notes       = excluded.notes,
        updated_at  = now();
