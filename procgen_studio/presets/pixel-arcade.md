# pixel-arcade: sprites through the pixel grid DSL
description: crisp 16-32px pixel sprites drawn as palette grids, scaled with nearest-neighbour
outline: 1px ink outline around every sprite silhouette, no anti-aliasing
lighting: light from the top-left; one highlight row on top surfaces, one shadow column on the right side
shading: at most 3 shades per material, no dithering unless asked, no gradients inside sprites
background: dark checkerboard of bg and bg2 in 32px cells
shape_language: chunky readable silhouettes, big head proportions for characters, symmetrical where it reads better
composition: sprite sheets show numbered frames in a row with a larger hero copy below
renderer: pixel

palette.bg: #1c1730
palette.bg2: #16122a
palette.ink: #1a1020
palette.base: #c4cede
palette.base2: #7884a0
palette.metal: #eef2fa
palette.metal2: #828caf
palette.accent: #ffd25a
palette.accent2: #be8228
palette.wood: #6e4628
palette.wood2: #46301c
palette.liquid: #28c8f0
palette.liquid2: #1a6a9a
palette.gem: #ff4a6a
palette.glow: #28c8f0
palette.highlight: #ffffff
palette.shadow: #0c0a16
palette.sky: #2a3a6a
palette.sky2: #4a6aa0
palette.ground: #3a2a20
palette.foliage: #3a8a4a
palette.light: #f0c8a0
