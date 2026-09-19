-- Prompt 3.2b-p -- 3.2b with a plastic check added, and nothing else changed.
--
-- The MEO part is 3.2b word for word (generated from app/main.py plus the
-- 3.2b paragraph, and checked against rig/prompt_3_2b.sql), so comparing 3.2b
-- with 3.2b-p on the same photos shows whether asking about plastic moves the
-- band. Two additions only: a plastic paragraph just before the answer format,
-- and a PLASTIQUE line before JUSTIFICATION.
--
-- Plastic is a presence question, not a percentage: fragments >= 3 mm (a fifth
-- of a larva), described by shape because that is what separates them from
-- everything organic -- sharp, angular, straight-edged, flat, often glossy, any
-- colour. Shed skins and feed husks are named as not plastic. It is reported
-- on its own line and must not change the band.
--
-- Caliban flags the tray if ANY rotation answers oui. Run
-- rig/plastic_columns.sql first so the flag is recorded.
--
-- Selectable under Gestion de l'échantillonnage once inserted; not active
-- until chosen there. Test it on the re-score tab before using it anywhere.
--
-- Updated 2026-09-19 to drop DENSITE, matching prompt_3_2b.sql -- see that
-- file's header for why. Editing in place, not a new label, for the same
-- reason: this is the db-sourced side of the prompt family, where that is
-- the established practice.

insert into vision_prompts (label, prompt_text, band_scale, notes)
values ('3.2b-p', $prompt$You are looking at a photograph of black soldier fly larvae (Hermetia illucens) scattered on a white tray, taken by a fixed calibration rig. Estimate how much MEO is visible -- organic foreign matter, the rearing residue also called frass.

The photograph is taken under controlled conditions you can rely on. The camera is directly overhead and square to the tray, the lighting is even across the whole frame, and the colours are fixed. The frame is 400 mm wide and the whole of it is tray. A larva is 15 to 20 mm long, so it spans roughly one twenty-fifth of the image width -- use that as your ruler.

Judge density against the area the sample itself covers -- larvae plus MEO -- and NOT against the whole photograph. The larvae are spread thinly so that they lie in a single layer, so a large part of the frame is bare white tray. Bare tray is empty space. It is neither contamination nor evidence of a clean sample, and it must not enter the estimate either way.

Count material you could see at a glance. Using the larva as a ruler, that means particles down to about 1 mm -- roughly one fifteenth of a larva's length. Anything finer than that is dust and powder residue: ignore it. Judge as an inspector glancing at the tray would, not as someone with a magnifying glass.

Two things look like MEO and are not.

A prepupa is a normal larva, not foreign matter. Approaching pupation a larva darkens considerably, to dark brown or nearly black, and by colour alone can resemble a clump of frass. Before counting anything dark as MEO, look at its shape: a larva keeps its elongated, clearly segmented outline even when very dark.

A crushed or broken larva is also product, not contamination. Larval flesh is pale and cream-coloured where breakage exposes it, plainly different from the brown-to-black of frass. Judge these by colour: a pale fragment is a broken larva and does not count, a dark one still does.

Be careful at the low end of the scale, where reading too high is the common mistake. A sample at 2-3% still shows MEO: several small dark pieces scattered here and there across the sample, while most larvae have nothing beside them. That is <3% -- it is NOT 3-8%. Only a sample with no visible MEO at all, or just one or two specks, sits at the very bottom of <3%. Reserve 3-8% for when foreign matter forms visible specks or clumps across a good part of the sample area -- dark material beside a fair share of the larvae, not just an occasional piece -- while the larvae still plainly dominate. At 8-13% dark material is a continuous presence rather than scattered incidents: there is some in every part of the sample, and it reads as a component of the mixture rather than as debris within it.

Do NOT try to count individual particles. Give an overall visual impression of density, as a person glancing at the tray would.

Separately from MEO, check the sample for plastic. What matters is plastic fragments at least a fifth of a larva's length (about 3 mm); smaller pieces do not matter here. They come from processing and are usually sharp and angular: straight edges, sharp corners, flat, often glossy or translucent, and of any colour -- including white, clear or black, which can blend into the tray or the frass. Nothing organic on this tray has that shape: larvae are rounded, segmented ovals, frass is crumbly rounded lumps, and broken larvae are soft-edged. A pale, papery, translucent shed larval skin is not plastic even when it is torn, and neither is a flat husk or hull from the feed. Judge by shape and edges first, colour second. Plastic does NOT change the MEO band -- report it only on the PLASTIQUE line.

Answer EXACTLY in this format, with nothing before or after:

BANDE: [a single value among : <3%, 3-8%, 8-13%, >13%]
CONFIANCE: [Faible, Moyenne, ou Élevée]
FACTEURS: [comma-separated list, only from : prepupes, fragments_ecrases, poussiere, densite_reelle, autre -- the factors that ACTUALLY influenced this band choice on this specific photo, not a generic list]
PLASTIQUE: [oui, non, ou incertain] -- if oui or incertain, add in a few French words what it looks like and where on the tray, e.g. « fragment blanc anguleux, coin supérieur gauche »
JUSTIFICATION: [one sentence, in French, what drove this choice]$prompt$,
        'coarse',
        '3.2b plus a plastic check (fragments >= 3 mm, judged by shape). MEO wording identical to 3.2b. No DENSITE.')
on conflict (label) do update
    set prompt_text = excluded.prompt_text,
        band_scale  = excluded.band_scale,
        notes       = excluded.notes,
        updated_at  = now();
