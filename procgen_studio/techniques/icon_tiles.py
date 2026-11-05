# technique: game icon set on rounded tiles (sword, potion, shield, key)
# use for: icon sets, inventory items, UI badges, achievement icons
# notes: every colour comes from PAL, so swapping the STYLE palette restyles the set without redrawing.
#        Each icon is authored in a 512x512 tile space and placed with a transform.

def draw(t=0.0):
    INK = PAL["ink"]
    S = f'stroke="{INK}" stroke-width="10" stroke-linejoin="round" stroke-linecap="round"'
    S7 = f'stroke="{INK}" stroke-width="7" stroke-linejoin="round"'

    def defs(i):
        return f'''
<radialGradient id="bg{i}" cx=".4" cy=".35" r=".8"><stop offset="0" stop-color="{PAL['bg']}"/><stop offset="1" stop-color="{PAL['bg2']}"/></radialGradient>
<radialGradient id="gl{i}" cx=".5" cy=".5" r=".5"><stop offset="0" stop-color="{PAL['glow']}" stop-opacity=".55"/><stop offset="1" stop-color="{PAL['glow']}" stop-opacity="0"/></radialGradient>
<linearGradient id="m{i}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{PAL['metal']}"/><stop offset="1" stop-color="{PAL['metal2']}"/></linearGradient>
<linearGradient id="g{i}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{PAL['accent']}"/><stop offset="1" stop-color="{PAL['accent2']}"/></linearGradient>
<linearGradient id="l{i}" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="{PAL['liquid']}"/><stop offset="1" stop-color="{PAL['liquid2']}"/></linearGradient>
<linearGradient id="w{i}" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{PAL['wood']}"/><stop offset="1" stop-color="{PAL['wood2']}"/></linearGradient>'''

    def tile(i, body):
        return f'''<rect x="16" y="16" width="480" height="480" rx="64" fill="url(#bg{i})" stroke="{INK}" stroke-width="8"/>
<rect x="30" y="30" width="452" height="452" rx="52" fill="none" stroke="{PAL['highlight']}" stroke-opacity=".08" stroke-width="4"/>
<circle cx="256" cy="256" r="200" fill="url(#gl{i})"/>
<ellipse cx="256" cy="440" rx="150" ry="18" fill="{PAL['shadow']}" opacity=".45"/>{body}'''

    def sword(i):
        return tile(i, f'''<g transform="translate(40 -10) rotate(45 256 256) translate(0 -10)">
<path d="M256 60 L290 110 L290 330 L222 330 L222 110 Z" fill="url(#m{i})" {S}/>
<path d="M256 70 L256 330" stroke="{PAL['highlight']}" stroke-opacity=".7" stroke-width="6"/>
<path d="M256 70 L290 110 L290 330 L256 330 Z" fill="{PAL['shadow']}" opacity=".18"/>
<rect x="176" y="326" width="160" height="34" rx="14" fill="url(#g{i})" {S}/>
<rect x="238" y="358" width="36" height="78" rx="8" fill="url(#w{i})" {S}/>
<path d="M240 375h32M240 395h32M240 415h32" stroke="{INK}" stroke-width="5"/>
<circle cx="256" cy="452" r="22" fill="url(#g{i})" {S}/>
<circle cx="256" cy="343" r="11" fill="{PAL['gem']}" stroke="{INK}" stroke-width="5"/>
<circle cx="250" cy="446" r="6" fill="{PAL['highlight']}" opacity=".8"/></g>''')

    def potion(i):
        flask = ("M220 120 h72 v70 C370 215 395 270 395 320 C395 400 330 445 256 445 "
                 "C182 445 117 400 117 320 C117 270 142 215 220 190 Z")
        bubbles = "".join(
            f'<circle cx="{150 + rng.random() * 210:.0f}" cy="{335 + rng.random() * 75:.0f}" '
            f'r="{4 + rng.random() * 7:.1f}" fill="{PAL["highlight"]}" opacity="{.3 + rng.random() * .3:.2f}"/>'
            for _ in range(5))
        return tile(i, f'''<path d="{flask}" fill="{PAL['highlight']}" fill-opacity=".12" {S}/>
<path d="M132 310 C170 290 220 330 256 312 C300 290 340 320 380 305 C388 390 330 430 256 430 C182 430 128 395 132 310 Z" fill="url(#l{i})"/>
<path d="M150 330 C200 345 230 330 256 340" stroke="{PAL['highlight']}" stroke-opacity=".35" stroke-width="6" fill="none"/>
{bubbles}
<path d="{flask}" fill="none" {S}/>
<path d="M165 260 C180 225 205 212 225 205" stroke="{PAL['highlight']}" stroke-opacity=".8" stroke-width="12" fill="none" stroke-linecap="round"/>
<rect x="206" y="78" width="100" height="52" rx="12" fill="url(#w{i})" {S}/>
<rect x="196" y="122" width="120" height="22" rx="10" fill="url(#g{i})" {S}/>''')

    def shield(i):
        outer = ("M256 70 C320 105 375 110 410 108 C410 260 370 380 256 450 "
                 "C142 380 102 260 102 108 C137 110 192 105 256 70 Z")
        inner = ("M256 112 C305 138 345 145 370 145 C366 262 335 350 256 405 "
                 "C177 350 146 262 142 145 C167 145 207 138 256 112 Z")
        return tile(i, f'''<g transform="translate(256 260) scale(.88) translate(-256 -260)">
<path d="{outer}" fill="url(#g{i})" {S}/>
<path d="{inner}" fill="url(#w{i})" {S7}/>
<path d="M256 112 L256 405 C335 350 366 262 370 145 C345 145 305 138 256 112 Z" fill="{PAL['shadow']}" opacity=".2"/>
<path d="M256 180 L300 250 L256 330 L212 250 Z" fill="{PAL['gem']}" {S7}/>
<path d="M256 190 L282 250 L256 250 Z" fill="{PAL['highlight']}" opacity=".55"/>
<path d="M130 130 C170 125 210 115 250 92" stroke="{PAL['highlight']}" stroke-opacity=".7" stroke-width="9" fill="none"/>
<circle cx="160" cy="170" r="8" fill="{INK}"/><circle cx="352" cy="170" r="8" fill="{INK}"/><circle cx="256" cy="425" r="8" fill="{INK}"/></g>''')

    def key(i):
        return tile(i, f'''<g transform="translate(-20 -15) rotate(-40 256 256)">
<rect x="236" y="190" width="40" height="250" rx="10" fill="url(#g{i})" {S}/>
<path d="M276 360 h50 v28 h-22 v24 h28 v28 h-56 Z" fill="url(#g{i})" {S}/>
<circle cx="256" cy="140" r="82" fill="url(#g{i})" {S}/>
<circle cx="256" cy="140" r="36" fill="url(#bg{i})" {S}/>
<path d="M200 105 A70 70 0 0 1 250 70" stroke="{PAL['highlight']}" stroke-opacity=".8" stroke-width="10" fill="none"/>
<path d="M246 200 v220" stroke="{PAL['highlight']}" stroke-opacity=".55" stroke-width="7"/>
<circle cx="256" cy="232" r="13" fill="{PAL['gem']}" stroke="{INK}" stroke-width="5"/></g>''')

    items = [sword, potion, shield, key]
    cols, rows = (4, 1) if W >= H * 1.6 else (1, 4) if H >= W * 1.6 else (2, 2)
    cell = min(W / cols, H / rows)
    ox, oy = (W - cell * cols) / 2, (H - cell * rows) / 2
    defs_all = "".join(defs(k) for k in range(len(items)))
    tiles = "".join(
        f'<g transform="translate({ox + (k % cols) * cell:.1f} {oy + (k // cols) * cell:.1f}) scale({cell / 512:.4f})">'
        f'{f(k)}</g>' for k, f in enumerate(items))
    return svg_doc(tiles, defs=defs_all, bg=PAL["shadow"])
