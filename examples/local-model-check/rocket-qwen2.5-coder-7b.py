def draw(t=0.0):
    s = min(W, H) / 1024.0
    cx, cy = W / 2, H * .5
    defs = f'''<radialGradient id="bg" cx=".5" cy=".5" r=".7"><stop offset="0" stop-color="{PAL['bg']}"/><stop offset="1" stop-color="{PAL['bg2']}"/></radialGradient>
<radialGradient id="gl"><stop offset="0" stop-color="{PAL['glow']}" stop-opacity=".45"/><stop offset="1" stop-color="{PAL['glow']}" stop-opacity="0"/></radialGradient>
<linearGradient id="m" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{PAL['metal']}"/><stop offset="1" stop-color="{PAL['metal2']}"/></linearGradient>
<linearGradient id="g" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{PAL['accent']}"/><stop offset="1" stop-color="{PAL['liquid']}"/></linearGradient>
<linearGradient id="w" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{PAL['wood']}"/><stop offset="1" stop-color="{PAL['wood2']}"/></linearGradient>'''
    u = s
    rocket = (f"M{cx + 100 * u:.1f},{cy - 200 * u:.1f} L{cx + 300 * u:.1f},{cy - 200 * u:.1f} L{cx + 200 * u:.1f},{cy - 400 * u:.1f} "
              f"L{cx + 80 * u:.1f},{cy - 400 * u:.1f} Z")
    star = (f"M{cx - 15 * u:.1f},{cy - 50 * u:.1f} L{cx - 10 * u:.1f},{cy + 15 * u:.1f} L{cx + 15 * u:.1f},{cy + 10 * u:.1f} "
            f"L{cx + 20 * u:.1f},{cy - 15 * u:.1f} Z")
    body = f'''<rect width="{W}" height="{H}" fill="url(#bg)"/>
<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{250 * u:.0f}" fill="url(#gl)"/>
<rect x="{cx - 120 * u:.1f}" y="{cy - 200 * u:.1f}" width="{440 * u:.1f}" height="{440 * u:.1f}" rx="{60 * u:.1f}" fill="url(#m)"/>
<path d="{rocket}" fill="url(#g)"/>
<path d="{star}" fill="{PAL['gem']}"/>
<path d="M{cx - 10 * u:.1f},{cy + 10 * u:.1f} L{cx + 10 * u:.1f},{cy + 10 * u:.1f} L{cx},{cy + 40 * u:.1f}" fill="url(#w)"/>
<path d="M{cx - 10 * u:.1f},{cy - 10 * u:.1f} L{cx + 10 * u:.1f},{cy - 10 * u:.1f} L{cx},{cy - 40 * u:.1f}" fill="url(#w)"/>
<text x="{cx:.0f}" y="{H * .7:.0f}" text-anchor="middle" font-family="Sora, Inter, DejaVu Sans, sans-serif" font-weight="700" font-size="{128 * u:.0f}" letter-spacing="{-4 * u:.1f}" fill="{PAL['metal']}">ROCKET</text>
<text x="{cx:.0f}" y="{H * .8:.0f}" text-anchor="middle" font-family="JetBrains Mono, DejaVu Sans Mono, monospace" font-size="{30 * u:.0f}" letter-spacing="{8 * u:.1f}" fill="{PAL['metal2']}">LAUNCH</text>'''
    return svg_doc(body, defs=defs)