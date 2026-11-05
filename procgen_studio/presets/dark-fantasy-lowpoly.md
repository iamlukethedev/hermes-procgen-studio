# dark-fantasy-lowpoly: faceted creatures and props
description: low-poly faceted dark-fantasy characters with ember lighting
outline: none on facets (facet stroke = facet fill to hide seams); thin ink edges only on claws and horns
lighting: strong top-left key light computed per facet (dot product of facet direction and light vector), hot under-glow from emissive cracks and eyes
shading: every body part is a polygon subdivided into jittered triangles, each shaded from base by its light term; bone and horn use a lighter material
background: deep radial vignette bg -> bg2 -> black, a large soft glow behind the head, drifting ember particles, a fog gradient over the lower quarter
shape_language: hulking asymmetric silhouette, small head, long arms, sharp horns and spines, readable at thumbnail size
composition: full-body hero shot, centred, feet on an elliptical ground shadow, optional title in a serif font along the bottom
renderer: python

palette.bg: #4a1420
palette.bg2: #1a0810
palette.ink: #1a1218
palette.base: #463e4e
palette.base2: #282030
palette.metal: #8a8296
palette.metal2: #4a4456
palette.accent: #c9a27a
palette.accent2: #7a5a40
palette.wood: #c8baa0
palette.wood2: #8a7a60
palette.liquid: #ff6a2e
palette.liquid2: #a01e10
palette.gem: #ffb02e
palette.glow: #ff3b2f
palette.highlight: #ffe26a
palette.shadow: #050206
palette.sky: #2a0e18
palette.sky2: #4a1420
palette.ground: #1a0a10
palette.foliage: #2a1a22
palette.light: #ff7a3a
