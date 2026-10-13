-- Prompt chute-v1 -- the first prompt written for the inline rig's view of
-- the chute, not for a dish or a tray.
--
-- Written from the 46 inline photos of 2026-10-13 (09:11-11:08, HQ camera,
-- flash, 1332x990), looked at one by one. What the tray prompts (3.2b/3.4b,
-- which the inline readings used until now) get wrong about this view:
--
--   * The product is IN FREE FALL over the chute, not lying on a surface.
--     Every larva casts a shadow on the chute below it, offset, larva-shaped
--     and dark grey-black -- the single most frass-like thing in the photo
--     that is not frass.
--   * The chute surface has its own permanent marks: a rust-brown film of
--     residue across the top of the frame, white specks, cracks, stains, and
--     a bright reflection of the flash low in the centre. In an empty-chute
--     photo all of it is visible and none of it is product. The brown film
--     looks like fine frass.
--   * The stream comes in two falls, left and right, with a sparse gap
--     between, and its density changes a lot from one minute to the next.
--     There is no rim and no "inside the dish".
--   * Frass in the stream: separate crumbly clumps, grey-brown to tan, matte,
--     irregular, from crumbs to half a larva long -- the same material the
--     tray prompts describe, seen in the air.
--
-- UNCALIBRATED. No lab value was available for any of these photos when this
-- was written. The band descriptions carry over the tray prompts' clump-
-- density reasoning (3.4b/3.4c) adapted to a falling stream; the band values
-- will need fitting against lot lab ME% once inline readings have been
-- matched to lots (the same production-window rule as the tray samples).
--
-- English instructions, French output fields (CLAUDE.md). Same answer format
-- and 6-band scale (coarse6) as 3.2b/3.4b, so Caliban, the destoner screen
-- and the En ligne tab need no change, and inline and tray bands compare.
--
-- Editing in place later is fine (db-sourced: prompt_hash and
-- vision_prompt_history keep traceability); a substantive rewrite gets a new
-- label (chute-v2).
--
-- To make the inline rig use it (one prompt, not the tray's two-prompt rule;
-- the tray rig is not affected), after running this:
--
--   insert into system_config (key, value)
--   values ('inline_settings', '{"mode": "shadow", "interval_min": 1, "variant": "chute-v1",
--                                "high_variant": "", "low_variant": ""}')
--   on conflict (key) do update set value = excluded.value;
--
-- (keep whatever interval_min / repeats you are using in that JSON.)

insert into vision_prompts (label, prompt_text, band_scale, notes)
values ('chute-v1', $prompt$You are looking at a flash photograph taken by a fixed camera mounted directly above an inclined grey steel chute, just after the destoner on a black soldier fly (Hermetia illucens) larvae production line. Larvae fall through the frame in a stream, together with whatever organic foreign matter (MEO) is mixed in with them -- the dried rearing residue also called frass. Estimate how much MEO is in the falling product.

How the photograph is made, so you know what you are looking at. The flash freezes the falling product, so every larva and particle in the stream is sharp. The frame is about 22 cm wide; a larva is 15 to 20 mm long, roughly one twelfth to one fifteenth of the image width -- use that as your ruler. The colours are fixed and reliable.

Judge ONLY the product falling through the frame: the larvae and the loose particles travelling with them. Everything else in the photograph is the chute and must be ignored completely:

- Shadows. Each falling larva and particle casts a shadow onto the chute below it. Shadows are dark grey to black, soft-edged, flat, and shaped like the object that casts them -- most are larva-shaped -- and they all lie offset in the same direction from their objects. They have no texture and no colour of their own. A shadow is NEVER MEO, however dark.
- The chute surface. It is matte grey steel with permanent marks: a rust-brown film of fine residue along the upper part of the frame, small white specks, hairline cracks, darker stains and worn patches. These marks are part of the chute, they look the same in every photograph, and they are not in the stream. The rust-brown film in particular is NOT falling frass: it is stuck to the surface, flat and evenly spread, with no clumps standing out of it.
- The bright patch. The flash reflects off the chute as a bright, washed-out area, usually low in the centre of the frame. Ignore it, and do not read the product inside it as paler or cleaner than elsewhere.
- Empty chute. The stream usually falls in two bands, left and right, with a thinner gap between them, and how much product passes changes from one moment to the next. Empty or sparse chute is not evidence of a clean sample, and it is not contamination either: judge MEO against the product that IS in the stream -- larvae plus MEO -- never against the area of the photograph.

What MEO looks like in the stream -- read this carefully. MEO here is NOT mainly black or dark. It is dried residue: separate, matte, porous, crumbly lumps with irregular, ragged outlines, like crumbs of dried soil or bark. Its colour is grey-brown to greyish-tan, duller and greyer than the larvae, sometimes flecked with darker specks. It has no gloss, no segments and no elongated tapered shape. Pieces range from crumbs of about 1 mm to clumps half a larva long, and a falling clump casts its own small shadow, just as a larva does. Count material you could see at a glance -- particles down to about 1 mm, roughly one fifteenth of a larva's length -- and ignore anything finer, which is dust.

What is NOT MEO:

Larvae are glossy, elongated, tapered at both ends and clearly segmented, golden-tan with darker brown bands. Segment bands, darker tail ends and dark areas where falling larvae overlap are all larva.

Prepupae are larvae approaching pupation: the same elongated, segmented shape, but dark brown to nearly black. They are product, and usually the darkest objects in the stream. A dark colour is therefore NOT a sign of MEO -- judge by shape and texture, never by darkness alone.

Broken or crushed larvae are product too: their exposed flesh is smooth, glossy and cream to yellow, and torn skin still shows segment lines. Frass is matte and crumbly; larval pieces are glossy and smooth.

The bands are MEO percentage by WEIGHT, measured in the lab on the bagged product. MEO shows much less in a photograph than its share of the weight, so even a stream at 8-13% still looks mostly like larvae -- do not wait for frass to rival the larvae before choosing a higher band. At the low end the opposite mistake is the common one: a few clumps you can point to are still <3%.

What each band looks like, judged on the product in the stream:

<3% -- Only larvae, often with some dark prepupae, plus at most a few small crumbs and one or two larger clumps you could point to one by one. A single big clump on an otherwise clean stream is still <3%.

3-5% -- Several separate clumps scattered through the stream, more than you would want to point to one by one, but each one an isolated incident with clean stretches of larvae between them.

5-8% -- Clumps turn up throughout the stream, including several medium clumps a quarter to a third of a larva long; few parts of the stream are entirely free of them.

8-10% -- Crumbly grey-brown clumps in every part of the stream, several near almost any group of larvae, many of them 3 to 10 mm -- a quarter to half a larva long. The larvae still dominate the picture; that is normal here.

10-13% -- Clumps are frequent and sizeable everywhere, some falling together in loose groups; MEO is plainly part of the stream rather than separate incidents, though larvae are still the majority.

>13% -- Clumps are abundant enough to compete with the larvae for attention, falling as patches and loose mats rather than separate lumps.

When torn between two neighbouring bands, decide by how widespread the clumps are through the stream rather than by any single large clump, and remember that the lab measures weight.

Do NOT try to count individual particles. Give an overall impression of how much MEO is in the falling product by weight, as an experienced inspector watching the stream would.

Answer EXACTLY in this format, with nothing before or after:

BANDE: [a single value among : <3%, 3-5%, 5-8%, 8-10%, 10-13%, >13%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [comma-separated list, only from : prepupes, fragments_ecrases, ombres, residu_chute, flux_clairseme, poussiere, autre -- the factors that ACTUALLY influenced this band choice on this specific photo, not a generic list]
JUSTIFICATION: [one sentence, in French, what drove this choice]$prompt$,
        'coarse6',
        'First chute prompt: judge only the falling stream; ignore shadows (offset, larva-shaped), the chute surface and its rust-brown residue film, the flash reflection and empty chute. Uncalibrated -- written from 46 photos of 2026-10-13 with no lab values.')
on conflict (label) do update
    set prompt_text = excluded.prompt_text,
        band_scale  = excluded.band_scale,
        notes       = excluded.notes,
        updated_at  = now();
