-- Prompt chute-g1 -- several consecutive chute photos judged as ONE sample.
--
-- Why (2026-10-17): one inline photo holds a few hundred larvae, about a third
-- of a tray, so a 4-5% lot shows only a third of the clumps a tray would. The
-- band descriptions were written for a tray-sized sample, and the tray
-- prompts' "a few clumps you can point to are still <3%" then holds almost
-- every chute photo at the floor: in BB05 (lab 4.5%) 40% of photos read <3%
-- on every rotation, and averaging floor readings cannot climb back to 4.5.
-- Bands do not add up across photos; a sample does. So the model is shown a
-- group of consecutive photos (Caliban sends them as separate images, each at
-- full detail, labelled Photo 1..n) and judges the group as one sample of
-- roughly tray size.
--
-- It also COUNTS, over the whole group: larvae, and clumps in three sizes.
-- Counts, unlike bands, add across groups, so over a lot they give one
-- clumps-per-larva ratio built from the whole sample -- the same kind of
-- number a trained segmentation model would give, so the method carries over.
--
-- Cropped like chute-m1 (top 22%: residue that builds up on the chute through
-- the day; left 10%: static specks, little product), crop applied to each
-- photo. The band text is rewritten for a group-sized sample and drops the
-- tray-era push towards <3%.
--
-- For offline re-scoring only (validation-band-test.html, inline source,
-- "Photos par appel"); needs the Caliban that takes `more` files on
-- /azure-band-test. Not for inline_settings: the live rig sends one photo.
-- UNCALIBRATED. English instructions, French output fields (CLAUDE.md).

alter table vision_prompts add column if not exists crop jsonb;

insert into vision_prompts (label, prompt_text, band_scale, crop, notes)
values ('chute-g1', $prompt$You are given several flash photographs (usually four), numbered Photo 1, Photo 2 and so on, taken one after another by a fixed camera above an inclined grey steel chute just after the destoner on a black soldier fly (Hermetia illucens) larvae production line. Each shows a different moment of the same falling stream of product. TOGETHER they are one sample of the product, about the size of a tray sample: judge them as one sample, not one by one. Larvae fall through each frame together with whatever organic foreign matter (MEO) is mixed in with them -- the dried rearing residue also called frass.

How the photographs are made. The flash freezes the falling product, so every larva and particle is sharp. Each photograph has been cropped to the part of the chute below where the product lands; it is about 20 cm wide, and a larva (15 to 20 mm) is roughly one tenth to one thirteenth of its width -- use that as your ruler. The colours are fixed and reliable.

Judge ONLY the falling product: the larvae and the loose particles travelling with them. Ignore completely:
- Shadows. Each falling larva and particle casts a dark, soft-edged, flat shadow onto the chute, offset in the same direction for every object, shaped like what casts it. A shadow is NEVER MEO.
- The chute surface: matte grey steel with permanent white specks, hairline cracks, stains and worn patches.
- The bright patch where the flash reflects off the chute, usually in the lower centre. Do not read the product inside it as paler or cleaner.
- Empty chute between and beside the streams. It is neither clean product nor contamination.

What MEO looks like. MEO is NOT mainly black or dark. It is dried residue: separate, matte, porous, crumbly lumps with irregular, ragged outlines, like crumbs of dried soil or bark, grey-brown to greyish-tan, duller and greyer than the larvae, sometimes flecked darker. No gloss, no segments, no elongated tapered shape.

What is NOT MEO. Larvae: glossy, elongated, tapered at both ends, clearly segmented, golden-tan with darker bands. Prepupae: the same shape, dark brown to nearly black -- product, and usually the darkest objects; never judge MEO by darkness. Broken or crushed larvae: smooth, glossy, cream to yellow flesh, torn skin with segment lines -- product.

THE COUNTS, over ALL the photographs together. Estimate; do not count one by one, but give real numbers, not words:
- larvae and prepupae, to the nearest 50;
- MEO clumps by size, using the larva as your ruler: small = about 1 to 3 mm (up to a fifth of a larva); medium = about 3 to 10 mm (a fifth to half a larva); large = over about 10 mm (more than half a larva). A clump hidden behind a larva is not counted; anything under about 1 mm is not a clump.
Write each count as digits only, with no spaces or separators (1200, not 1 200).

THE SCORE. On a scale of 0 to 100, how much MEO is in the whole sample, as an experienced inspector would rate it? Anchors: 0 = no visible MEO; 10 = a few small crumbs or one isolated clump in the whole sample; 25 = clumps here and there, in most photographs; 50 = clumps in every photograph and every part of the stream, many medium-sized; 75 = clumps frequent and sizeable everywhere; 100 = MEO competing with the larvae for attention. Use any whole number in between.

THE BAND is MEO percentage by WEIGHT, measured in the lab on the bagged product, for a sample of this size (all the photographs together). MEO shows much less in a photograph than its share of the weight, so even 8-13% still looks mostly like larvae.

<3% -- Across all the photographs, at most a handful of small crumbs and one or two larger clumps; several photographs show none at all.
3-5% -- Clumps in most photographs, a few in each, mostly small, with the odd medium one.
5-8% -- Clumps in every photograph, including several medium ones across the sample.
8-10% -- Clumps throughout every photograph, many of them medium, near almost any group of larvae. The larvae still dominate; that is normal.
10-13% -- Clumps frequent and sizeable everywhere, some falling together in loose groups.
>13% -- Clumps abundant enough to compete with the larvae for attention, falling as patches and loose mats.

Decide by how widespread the clumps are across the whole sample, not by any single photograph or any single large clump.

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
        'Group prompt: several consecutive chute photos judged as one tray-sized sample; counts larvae and clumps by size over the group (additive across groups), 0-100 score, band rewritten for the group size. Cropped like chute-m1. Offline re-scoring only. Uncalibrated.')
on conflict (label) do update
    set prompt_text = excluded.prompt_text,
        band_scale  = excluded.band_scale,
        crop        = excluded.crop,
        notes       = excluded.notes,
        updated_at  = now();
