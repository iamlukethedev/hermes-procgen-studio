# technique: animated logo reveal (stroke draw-on, staggered fades, eased scale)
# use for: animated logos, intros, loaders, motion graphics, short loops
# notes: draw(t) is called once per frame with t in [0, 1]; stagger each element with its own eased
#        window and keep the final frame identical to the static logo.

def draw(t=0.0):
    s = min(W, H) / 1024.0
    cx, cy = W / 2, H * .39
    a, b, c, d = ease(t / .45), ease((t - .3) / .25), ease((t - .5) / .2), ease((t - .6) / .25)
    blink = 1 if t < .7 or int(t * 24) % 2 == 0 else 0
    perimeter = 6 * 165 * s
    defs = f'''<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{PAL['accent']}"/><stop offset="1" stop-color="{PAL['liquid']}"/></linearGradient>
<radialGradient id="bg" cx=".5" cy=".38" r=".7"><stop offset="0" stop-color="{PAL['bg']}"/><stop offset="1" stop-color="{PAL['bg2']}"/></radialGradient>
<radialGradient id="halo"><stop offset="0" stop-color="{PAL['glow']}" stop-opacity=".45"/><stop offset="1" stop-color="{PAL['glow']}" stop-opacity="0"/></radialGradient>'''
    sc = (0.6 + 0.4 * c) * c
    body = f'''<rect width="{W}" height="{H}" fill="url(#bg)"/>
<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{330 * s:.0f}" fill="url(#halo)" opacity="{a:.3f}"/>
<polygon points="{poly_pts(cx, cy, 165 * s)}" fill="{PAL['ink']}" opacity="{a:.3f}"/>
<polygon points="{poly_pts(cx, cy, 165 * s)}" fill="none" stroke="url(#g)" stroke-width="{30 * s:.1f}" stroke-linejoin="round" stroke-dasharray="{perimeter * a:.1f} {perimeter:.1f}"/>
<g opacity="{b:.3f}" transform="translate({-40 * s * (1 - b):.1f} 0)"><path d="M{cx - 70 * s:.1f},{cy - 60 * s:.1f} L{cx - 5 * s:.1f},{cy:.1f} L{cx - 70 * s:.1f},{cy + 60 * s:.1f}" fill="none" stroke="url(#g)" stroke-width="{34 * s:.1f}" stroke-linecap="round" stroke-linejoin="round"/></g>
<rect x="{cx + 10 * s:.1f}" y="{cy + 38 * s:.1f}" width="{80 * s:.1f}" height="{30 * s:.1f}" rx="{15 * s:.1f}" fill="{PAL['liquid']}" opacity="{b * blink:.3f}"/>
<g transform="translate({cx + 75 * s:.1f} {cy - 62 * s:.1f}) scale({sc * s:.3f}) rotate({90 * (1 - c):.1f})"><path d="M0,-38 Q5,-5 33,0 Q5,5 0,38 Q-5,5 -33,0 Q-5,-5 0,-38 Z" fill="{PAL['highlight']}"/></g>
<g opacity="{d:.3f}" transform="translate(0 {20 * s * (1 - d):.1f})">
<text x="{cx:.0f}" y="{H * .703:.0f}" text-anchor="middle" font-family="Sora, Inter, DejaVu Sans, sans-serif" font-weight="700" font-size="{128 * s:.0f}" letter-spacing="{-4 * s:.1f}" fill="{PAL['metal']}">forgeline</text>
<text x="{cx:.0f}" y="{H * .772:.0f}" text-anchor="middle" font-family="JetBrains Mono, DejaVu Sans Mono, monospace" font-size="{30 * s:.0f}" letter-spacing="{8 * s:.1f}" fill="{PAL['metal2']}">SHIP AGENTS, NOT GLUE</text></g>'''
    return svg_doc(body, defs=defs)