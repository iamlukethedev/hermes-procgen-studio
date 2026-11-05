# Procgen Studio: 90-second demo

A screen recording script with the exact commands and prompts. Total runtime about 90 s of
footage; model calls are cut down in editing (mark the cuts with ✂).

## Before recording

```bash
# 1. Hermes with a capable model already configured (hermes model), plus the plugin.
hermes plugins install iamlukethedev/hermes-procgen-studio --enable
hermes config set image_gen.provider procgen-studio
hermes config set video_gen.provider procgen-studio
hermes tools enable video_gen

# 2. A clean project folder with the style contract.
mkdir -p ~/demo-dungeon && cd ~/demo-dungeon
hermes procgen init ember          # writes STYLE.md with "palette: ember"

# 3. Start the gateway in a second terminal if no service is installed.
hermes gateway run
```

Pre-generate once before recording so cached terminal history and font caches are warm, then
`rm -rf ~/.hermes/plugin-data/procgen-studio` for a clean `/procgen list`. Use a large terminal
font and an image viewer that auto-reloads (or the Desktop app with the procgen pane open on the
right).

## Script

| Time | On screen | Say |
|---|---|---|
| 0:00 | Terminal in `~/demo-dungeon`, `cat STYLE.md` | "This is Procgen Studio, an image backend for Hermes. No image API: my own model writes drawing code and it renders locally." |
| 0:08 | `hermes config get image_gen.provider` → `procgen-studio` | "It plugs in as the image provider, so every Hermes flow that makes images now uses it." |
| 0:12 | `hermes` chat, type prompt **P1** (below) | "Four game icons, in the style this project's STYLE.md defines." |
| ✂ | cut the model wait | |
| 0:20 | The PNG opens; terminal shows the tool result with `seed`, `source`, `render_id` | "Every image comes with the program that drew it, its seed and metadata." |
| 0:26 | `/procgen show last` (critic score, attempts, source path) | "A critic checked it against the prompt and the style, and would have redrawn it if it fell short." |
| 0:33 | Edit `STYLE.md`: change `palette: ember` to `palette: frost`, save | "Now one line in the style contract." |
| 0:38 | `/procgen restyle --set dungeon-loot` → frost icons open | "The whole set restyles locally in a second. No model call, because the code reads its colours from the palette." |
| 0:46 | Prompt **P2** (scene) | "Scenes work the same way." |
| ✂ | cut the model wait | |
| 0:52 | Scene opens. `/procgen reroll last --seed 7` → variation opens | "Re-rolling a seed re-runs the program: a new star field and tree line, instantly, for free." |
| 1:00 | Prompt **P3** (logo), then **P4** (animate it) | "Logos, and with ffmpeg installed, motion." |
| ✂ | cut the waits | |
| 1:08 | MP4 plays (GIF alongside) | |
| 1:13 | Desktop app, procgen pane: click a render, type seed 42, **Re-roll** | "The Desktop pane shows each render, its source and seed, and re-rolls on a click." |
| 1:22 | `examples/gallery.png` full screen, then the install command | "No keys, editable, consistent, reproducible. Install it with one command." |
| 1:30 | End | |

## Prompts

- **P1** `Make a set of 4 game icons on rounded tiles: a sword, a health potion, a shield and a key. [set=dungeon-loot]`
- **P2** `A wide flat-illustration hero scene: a cabin in a moonlit pine forest at night, warm windows, a path to the door, layered hills and fog. [style=flat-illustration]`
- **P3** `A logo and wordmark for a developer tools startup called forgeline, tagline 'SHIP AGENTS, NOT GLUE': a hexagon mark with a terminal prompt glyph. [style=brand-minimal]`
- **P4** `Animate that logo: the hexagon outline draws on, the prompt glyph slides in, the sparkle pops, then the wordmark fades up. Use the last image as the source.`

For P4 the agent passes the previous image path as `image_url`; if it does not, paste the path
from P3's result into the prompt.

## Optional extras (if there is time)

- `/procgen edit last make the gem emerald green and the shield rim silver` revises the existing
  program instead of starting over.
- `/procgen source last | head -40` to show the drawing code on screen.
- A cron job (`hermes cron add ...`) or a Telegram chat asking for an icon, to show it works in
  every flow unchanged.

## If something goes wrong on camera

- Slow model: set `[critic=off]` in the prompt for a single model call, or pin a faster model to
  the **Procgen Studio drawing** slot in `hermes model`.
- A local model hitting Hermes's 420 s tool deadline: `hermes config set
  timeouts.tools.sequential_call 1200` and raise
  `plugins.entries.procgen-studio.settings.time_budget` to match.
- `hermes procgen status` shows the style in effect, ffmpeg and where renders are stored.
