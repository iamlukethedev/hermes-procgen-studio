---
name: procgen-art-direction
description: How to get polished results from the Procgen Studio image/video backend (code-drawn art), keep asset sets consistent with STYLE.md, and re-roll or restyle without new model calls.
---

# Procgen Studio art direction

Procgen Studio is the image backend when `image_gen.provider: procgen-studio`. Every
`image_generate` call makes the model write a drawing program (Python that returns SVG, a
PIL image or a numpy array, or a pixel-grid spec) which is rendered locally. Each result
comes back with a `render_id`, `seed`, `source` path and `metadata` path.

## Writing good prompts

- Name the subject, the count and the layout: "a set of 4 game icons (sword, potion,
  shield, key) on rounded tiles in a 2x2 grid".
- Say what kind of asset it is: icon, logo, wordmark, scene, pattern, sprite sheet,
  low-poly creature, diagram, loader animation. That picks the right technique reference.
- Put the brand name or title text in quotes. Keep text short.
- It is not a photo model. For "a photo of X", ask for a stylised illustration of X.
- Pixel art: say "pixel sprite" or add `[renderer=pixel]`; it is drawn as a palette grid.

## Prompt directives

Add any of these anywhere in the prompt; they are stripped before drawing:

- `[style=frost]` use a preset (or a path to a STYLE.md) for this image only
- `[seed=42]` fix the seed (random placement, jitter, star fields)
- `[set=dungeon-icons]` group renders so the whole set can be restyled or regenerated
- `[renderer=pixel]` / `[renderer=svg]` force an output format
- `[critic=off]` / `[redraws=3]` override the critic loop for this call

`aspect_ratio` maps to 1536x1024 (landscape), 1024x1024 (square), 1024x1536 (portrait);
`resolution: "2K"` doubles it. `complexity` (-100..100) controls detail density and
`thinking: "high"` gives the critic more redraw rounds.

## STYLE.md

A project `STYLE.md` (or `.procgen/STYLE.md`) with a `preset:` or `palette:` line is the art
contract for every render in that working directory. Changing one line (for example
`palette: ember` to `palette: frost`) restyles the set:

- palette-only change: `/procgen restyle --set <name>` re-renders locally, no model call
- rule change (lighting, outline, shape language): `/procgen regen --set <name>`

## After a render

- `/procgen reroll <render_id> --seed 7` new variation of the same program, instant, free
- pass the returned image path as `image_url` with an instruction to edit the drawing
  ("make the gem emerald green"); the existing source is revised, not redrawn from scratch
- `/procgen show <render_id>` prints paths, seed and the critic log
- `video_generate` with `image_url` set to a Procgen render animates that drawing
