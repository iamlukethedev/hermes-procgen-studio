# technique: layered flat-vector night scene (moonlit forest cabin)
# use for: hero images, landscapes, scenes, key art, wallpapers, backgrounds
# notes: depth from 3-5 parallax silhouette layers separated by gradient fog bands; glow is a
#        radial gradient (the renderer cannot blur); every random choice goes through the seeded rng.
import math


def draw(t=0.0):
    s = min(W, H) / 1024.0

    def ridge(y, amp, step):
        pts, x = [], -step
        while x <= W + step:
            pts.append((x, y + rng.uniform(-amp, amp) * s))
            x += step
        return pts

    def layer(base, amp, col):
        pts = ridge(base, amp, 60 * s)
        d = f"M0,{H} " + " ".join(f"L{x:.0f},{y:.0f}" for x, y in pts) + f" L{W},{H} Z"
        return f'<path d="{d}" fill="{col}"/>'

    def trees(base, h, n, col, avoid=None, wmul=1.0):
        out = ""
        for _ in range(n):
            x = rng.uniform(-30 * s, W + 30 * s)
            if avoid and avoid[0] < x < avoid[1]:
                continue
            th = h * s * rng.uniform(.7, 1.3)
            w = th * .28 * wmul
            y = base + rng.uniform(-10, 10) * s
            p = "".join(f"M{x - w * (1 - k * .25):.1f},{y - th * (k / 3) * .8:.1f} L{x:.1f},{y - th * (k / 3) * .8 - th * .5:.1f} "
                        f"L{x + w * (1 - k * .25):.1f},{y - th * (k / 3) * .8:.1f} Z " for k in range(3))
            out += (f'<path d="{p}" fill="{col}"/>'
                    f'<rect x="{x - w * .08:.1f}" y="{y - 2:.1f}" width="{w * .16:.1f}" height="{h * s * .15:.1f}" fill="{col}"/>')
        return out

    layers = [mix(PAL["foliage"], PAL["sky2"], .35), PAL["foliage"], PAL["ground"], shade(PAL["ground"], .7),
              PAL["ink"]]
    stars = "".join(
        f'<circle cx="{rng.uniform(0, W):.0f}" cy="{rng.uniform(0, H * .5):.0f}" r="{rng.choice([1, 1, 1.5, 2]) * s:.1f}" '
        f'fill="{PAL["highlight"]}" opacity="{rng.uniform(.3, .9):.2f}"/>' for _ in range(180))
    mx, my = W * .25, H * .22
    moon = (f'<circle cx="{mx:.0f}" cy="{my:.0f}" r="{260 * s:.0f}" fill="url(#moon)"/>'
            f'<circle cx="{mx:.0f}" cy="{my:.0f}" r="{70 * s:.0f}" fill="{PAL["highlight"]}"/>'
            f'<circle cx="{mx - 22 * s:.0f}" cy="{my - 16 * s:.0f}" r="{12 * s:.0f}" fill="{shade(PAL["highlight"], .9)}"/>')
    cx, cy = W * .62, H * .69
    u = s
    cabin = f'''<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{230 * u:.0f}" fill="url(#warm)"/>
<rect x="{cx - 110 * u:.0f}" y="{cy - 70 * u:.0f}" width="{220 * u:.0f}" height="{120 * u:.0f}" fill="{PAL['wood']}"/>
<path d="M{cx - 140 * u:.0f},{cy - 62 * u:.0f} L{cx:.0f},{cy - 170 * u:.0f} L{cx + 140 * u:.0f},{cy - 62 * u:.0f} Z" fill="{PAL['wood2']}"/>
<rect x="{cx + 55 * u:.0f}" y="{cy - 200 * u:.0f}" width="{26 * u:.0f}" height="{70 * u:.0f}" fill="{PAL['wood2']}"/>
<rect x="{cx - 80 * u:.0f}" y="{cy - 35 * u:.0f}" width="{50 * u:.0f}" height="{44 * u:.0f}" rx="{4 * u:.0f}" fill="{PAL['light']}"/>
<rect x="{cx + 25 * u:.0f}" y="{cy - 30 * u:.0f}" width="{50 * u:.0f}" height="{80 * u:.0f}" rx="{4 * u:.0f}" fill="{PAL['accent']}"/>
<circle cx="{cx - 150 * u:.0f}" cy="{cy + 12 * u:.0f}" r="{12 * u:.0f}" fill="{PAL['light']}"/>
<circle cx="{cx - 150 * u:.0f}" cy="{cy + 12 * u:.0f}" r="{60 * u:.0f}" fill="url(#warm)"/>'''
    smoke = "".join(
        f'<circle cx="{cx + (68 + k * 14 + math.sin(k + t * 6) * 10) * u:.0f}" cy="{cy - (215 + k * 28) * u:.0f}" '
        f'r="{(8 + k * 4) * u:.0f}" fill="{PAL["metal"]}" opacity="{.22 - k * .03:.2f}"/>' for k in range(6))
    flies = "".join(
        f'<circle cx="{rng.uniform(W * .2, W):.0f}" cy="{rng.uniform(H * .63, H * .93):.0f}" r="{3 * s:.1f}" '
        f'fill="{PAL["light"]}" opacity="{rng.uniform(.5, 1):.2f}"/>' for _ in range(25))
    path = (f'<path d="M{cx:.0f},{cy + 52 * u:.0f} C{cx - 40 * u:.0f},{cy + 140 * u:.0f} {cx - 260 * u:.0f},{cy + 180 * u:.0f} '
            f'{cx - 330 * u:.0f},{H} L{cx - 160 * u:.0f},{H} C{cx - 120 * u:.0f},{cy + 200 * u:.0f} {cx - 10 * u:.0f},'
            f'{cy + 130 * u:.0f} {cx + 20 * u:.0f},{cy + 52 * u:.0f} Z" fill="{PAL["wood2"]}" opacity=".7"/>')
    defs = f'''<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{PAL['sky']}"/><stop offset=".55" stop-color="{mix(PAL['sky'], PAL['sky2'], .55)}"/><stop offset="1" stop-color="{PAL['sky2']}"/></linearGradient>
<radialGradient id="moon"><stop offset="0" stop-color="{PAL['highlight']}"/><stop offset=".25" stop-color="{PAL['highlight']}" stop-opacity=".35"/><stop offset="1" stop-color="{PAL['highlight']}" stop-opacity="0"/></radialGradient>
<radialGradient id="warm"><stop offset="0" stop-color="{PAL['glow']}" stop-opacity=".55"/><stop offset="1" stop-color="{PAL['glow']}" stop-opacity="0"/></radialGradient>
<linearGradient id="fog" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{PAL['sky2']}" stop-opacity="0"/><stop offset="1" stop-color="{PAL['sky2']}" stop-opacity=".35"/></linearGradient>'''
    body = (f'<rect width="{W}" height="{H}" fill="url(#sky)"/>{stars}{moon}'
            f'{layer(H * .55, 40, layers[0])}{trees(H * .58, 150, 40, layers[0])}'
            f'<rect y="{H * .49:.0f}" width="{W}" height="{H * .2:.0f}" fill="url(#fog)"/>'
            f'{layer(H * .64, 25, layers[1])}{trees(H * .655, 200, 28, layers[1], avoid=(cx - 170 * u, cx + 160 * u))}'
            f'<rect y="{H * .59:.0f}" width="{W}" height="{H * .18:.0f}" fill="url(#fog)"/>'
            f'{layer(H * .735, 12, layers[2])}{smoke}{cabin}{path}{flies}'
            f'{trees(H * 1.0, 420, 14, layers[4], avoid=(W * .29, W * .98), wmul=1.1)}')
    return svg_doc(body, defs=defs)