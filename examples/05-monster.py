# technique: low-poly faceted creature (dark-fantasy monster)
# use for: monsters, characters, creatures, low-poly props, faceted crystals and rocks
# notes: each body part is a polygon split into jittered triangles; a per-facet light term
#        (dot product with a top-left light vector) does the shading. Facet stroke = facet fill
#        hides seams. Glows are radial gradients. Authored in 1024 space and scaled.
import math


def draw(t=0.0):
    L = (-.6, -.8)
    s = min(W, H) / 1024.0
    ox, oy = (W - 1024 * s) / 2, (H - 1024 * s) / 2

    def facets(poly, base, sub=2):
        cx = sum(p[0] for p in poly) / len(poly) + rng.uniform(-8, 8)
        cy = sum(p[1] for p in poly) / len(poly) + rng.uniform(-8, 8)
        tris = [((cx, cy), poly[i], poly[(i + 1) % len(poly)]) for i in range(len(poly))]
        for _ in range(sub):
            nt = []
            for a, b, c in tris:
                def m(p, q):
                    return ((p[0] + q[0]) / 2 + rng.uniform(-5, 5), (p[1] + q[1]) / 2 + rng.uniform(-5, 5))
                ab, bc, ca = m(a, b), m(b, c), m(c, a)
                nt += [(a, ab, ca), (ab, b, bc), (ca, bc, c), (ab, bc, ca)]
            tris = nt
        rad = max(math.hypot(p[0] - cx, p[1] - cy) for p in poly)
        out = ""
        for a, b, c in tris:
            tx, ty = (a[0] + b[0] + c[0]) / 3 - cx, (a[1] + b[1] + c[1]) / 3 - cy
            r = math.hypot(tx, ty) / rad
            d = (tx * L[0] + ty * L[1]) / (math.hypot(tx, ty) + 1e-6)
            col = shade(base, .55 + .55 * d * r + .25 * (1 - r) + rng.uniform(-.08, .08))
            out += (f'<polygon points="{a[0]:.1f},{a[1]:.1f} {b[0]:.1f},{b[1]:.1f} {c[0]:.1f},{c[1]:.1f}" '
                    f'fill="{col}" stroke="{col}" stroke-width="2.2" stroke-linejoin="round"/>')
        return out

    FLESH, DARK, BONE = PAL["base"], PAL["base2"], PAL["wood"]
    parts = [
        ([(380, 430), (460, 360), (580, 350), (680, 410), (720, 520), (690, 650), (600, 720), (470, 720), (380, 650), (350, 540)], FLESH),
        ([(330, 420), (420, 370), (470, 430), (430, 520), (340, 510)], FLESH),
        ([(640, 380), (730, 400), (760, 480), (700, 530), (630, 470)], FLESH),
        ([(340, 500), (420, 520), (360, 700), (300, 800), (250, 790), (300, 650)], DARK),
        ([(700, 520), (760, 500), (790, 650), (810, 790), (760, 800), (720, 650)], DARK),
        ([(470, 300), (512, 250), (560, 300), (575, 380), (512, 420), (450, 380)], FLESH),
        ([(440, 700), (510, 710), (490, 850), (420, 880), (400, 860)], DARK),
        ([(530, 710), (610, 700), (640, 860), (620, 880), (550, 850)], DARK),
    ]
    horns = [[(478, 295), (420, 200), (380, 120), (440, 190), (495, 270)],
             [(546, 295), (604, 200), (644, 120), (584, 190), (529, 270)]]
    claws = "".join(
        f'<polygon points="{x},{y} {x + dx},{y + 60} {x + dx * 1.6},{y + 20}" fill="{PAL["wood"]}" stroke="{PAL["ink"]}" stroke-width="2"/>'
        for x, y, dx in [(250, 790, -8), (270, 795, 4), (290, 798, 14), (760, 800, -10), (782, 800, 2), (805, 795, 14)])
    spines = "".join(
        f'<polygon points="{x - 14},{y} {x + rng.uniform(-10, 10):.0f},{y - 60 - rng.uniform(0, 30):.0f} {x + 14},{y}" fill="{PAL["wood2"]}"/>'
        for x, y in [(395, 400), (430, 380), (594, 380), (629, 400)])
    body = "".join(facets(p, c) for p, c in parts) + "".join(facets(h, BONE, 1) for h in horns)
    pulse = .55 + .15 * math.sin(t * math.tau)
    eyes = "".join(
        f'<circle cx="{x}" cy="335" r="60" fill="url(#eg)"/>'
        f'<polygon points="{x - 16},328 {x + 16},332 {x + 8},344 {x - 12},342" fill="{PAL["highlight"]}"/>' for x in (488, 536))
    embers = "".join(
        f'<circle cx="{rng.uniform(0, 1024):.0f}" cy="{rng.uniform(300, 1000) - t * 80:.0f}" r="{rng.uniform(1.5, 4):.1f}" '
        f'fill="{PAL["light"]}" opacity="{rng.uniform(.3, .9):.2f}"/>' for _ in range(70))
    crack = (f'<circle cx="512" cy="540" r="110" fill="url(#eg)" opacity="{pulse:.2f}"/>'
             f'<path d="M512,470 L498,520 L522,560 L505,610" stroke="{PAL["gem"]}" stroke-width="6" fill="none" stroke-linejoin="round"/>'
             f'<path d="M498,520 L470,540 M522,560 L552,575" stroke="{PAL["liquid"]}" stroke-width="4"/>')
    defs = f'''<radialGradient id="bg" cx=".5" cy=".45" r=".75"><stop offset="0" stop-color="{PAL['bg']}"/><stop offset=".55" stop-color="{PAL['bg2']}"/><stop offset="1" stop-color="{PAL['shadow']}"/></radialGradient>
<radialGradient id="eg"><stop offset="0" stop-color="{PAL['gem']}" stop-opacity=".9"/><stop offset="1" stop-color="{PAL['glow']}" stop-opacity="0"/></radialGradient>
<linearGradient id="fog" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{PAL['sky']}" stop-opacity="0"/><stop offset="1" stop-color="{PAL['sky']}" stop-opacity=".95"/></linearGradient>
<radialGradient id="aura"><stop offset="0" stop-color="{PAL['glow']}" stop-opacity=".35"/><stop offset="1" stop-color="{PAL['glow']}" stop-opacity="0"/></radialGradient>'''
    scene = f'''<rect width="1024" height="1024" fill="url(#bg)"/>
<circle cx="512" cy="330" r="420" fill="url(#aura)"/>
<ellipse cx="512" cy="890" rx="330" ry="40" fill="{PAL['shadow']}" opacity=".6"/>
{spines}{body}{crack}{claws}{eyes}
<rect y="760" width="1024" height="264" fill="url(#fog)"/>{embers}
<text x="512" y="985" text-anchor="middle" font-family="Cinzel, Liberation Serif, DejaVu Serif, serif" font-weight="700" font-size="40" letter-spacing="10" fill="{PAL['accent']}" opacity=".85">THE HOLLOW WARDEN</text>'''
    return svg_doc(f'<rect width="{W}" height="{H}" fill="{PAL["shadow"]}"/>'
                   f'<g transform="translate({ox:.1f} {oy:.1f}) scale({s:.4f})">{scene}</g>', defs=defs)