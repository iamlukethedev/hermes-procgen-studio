def draw(t=0.0):
    s = min(W, H) / 1024.0
    cx, cy = W / 2, H * .39
    defs = f'''<radialGradient id="bg" cx=".5" cy=".38" r=".7"><stop offset="0" stop-color="{PAL['bg']}"/><stop offset="1" stop-color="{PAL['bg2']}"/></radialGradient>
<radialGradient id="halo"><stop offset="0" stop-color="{PAL['glow']}" stop-opacity=".45"/><stop offset="1" stop-color="{PAL['glow']}" stop-opacity="0"/></radialGradient>
<linearGradient id="m" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="{PAL['metal']}"/><stop offset="1" stop-color="{PAL['metal2']}"/></linearGradient>'''
    body = f'''<rect width="{W}" height="{H}" fill="url(#bg)"/>
<circle cx="{cx:.0f}" cy="{cy:.0f}" r="{330 * u:.0f}" fill="url(#halo)"/>
<polygon points="{poly_pts(cx, cy, 180 * u)}" fill="url(#m)" stroke="{PAL['highlight']}" stroke-opacity=".25" stroke-width="{3 * u:.1f}" stroke-linejoin="round"/>
<polygon points="{poly_pts(cx, cy, 150 * u)}" fill="{PAL['ink']}"/>
<path d="M{cx - 70 * u:.1f},{cy - 60 * u:.1f} L{cx - 5 * u:.1f},{cy:.1f} L{cx - 70 * u:.1f},{cy + 60 * u:.1f}" fill="none" stroke="url(#m)" stroke-width="{34 * u:.1f}" stroke-linecap="round" stroke-linejoin="round"/>
<rect x="{cx + 10 * u:.1f}" y="{cy + 38 * u:.1f}" width="{80 * u:.1f}" height="{30 * u:.1f}" rx="{15 * u:.1f}" fill="{PAL['liquid']}"/>
<path d="{spark}" fill="{PAL['highlight']}"/>
<text x="{cx:.0f}" y="{H * .703:.0f}" text-anchor="middle" font-family="Sora, Inter, DejaVu Sans, sans-serif" font-weight="700" font-size="{128 * u:.0f}" letter-spacing="{-4 * u:.1f}" fill="{PAL['metal']}">{name}</text>
<text x="{cx:.0f}" y="{H * .772:.0f}" text-anchor="middle" font-family="JetBrains Mono, DejaVu Sans Mono, monospace" font-size="{30 * u:.0f}" letter-spacing="{8 * u:.1f}" fill="{PAL['metal2']}">{tagline}</text>'''
    return svg_doc(body, defs=defs)