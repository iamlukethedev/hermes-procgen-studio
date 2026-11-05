# Examples

Every PNG here came out of Hermes's built-in `image_generate` tool with
`image_gen.provider: procgen-studio`, driven by `hermes -z "<prompt>"` from a project
folder containing [`STYLE.md`](STYLE.md). Next to each image are the drawing program that
produced it (`.py`), the sidecar metadata Procgen Studio writes (`.procgen.json`: prompt, seed,
size, style, palette) and the render log (`.log.txt`: record path, critic result, model calls).

| File | Prompt (directives in brackets are parsed by the plugin) |
|---|---|
| `01-icons-ember` | Generate a set of 4 game icons on rounded tiles: a sword, a health potion, a shield and a key. `[set=dungeon-loot]` |
| `02-icons-frost` | The same prompt after changing one line of `STYLE.md`: `palette: ember` to `palette: frost` |
| `03-scene` | A wide flat-illustration hero scene: a cabin in a moonlit pine forest at night, warm windows, a path leading to the door, layered hills and fog. `[style=flat-illustration]` |
| `04-logo` | A logo and wordmark for a developer tools startup called forgeline, tagline 'SHIP AGENTS, NOT GLUE': a hexagon mark with a terminal prompt glyph. `[style=brand-minimal]` |
| `05-monster` | A low-poly dark fantasy monster called THE HOLLOW WARDEN: hulking horned golem with glowing eyes and an ember crack in its chest, full body. `[style=dark-fantasy-lowpoly]` |
| `06-scene-reroll-seed7` | `hermes procgen reroll <scene id> --seed 7`: the scene's program re-run locally with a new seed, no model call |

`gallery.png`, `presets.png` and `banner.png` are built from these files by
`scripts/make_gallery.py` (`presets.png` renders each preset's reference technique locally).

## Reproduce

```bash
hermes gateway run            # in another terminal, if no gateway service is installed
bash scripts/e2e.sh           # uses the model Hermes is configured with; writes into examples/
```

## Provenance of the committed run

The machine that produced this commit had no model credentials, so the committed run used
`E2E_REPLAY=1 bash scripts/e2e.sh`. That mode points Hermes at
[`scripts/replay_llm.py`](../scripts/replay_llm.py), a local OpenAI-compatible endpoint that
stands in for the model: it issues the agent's `image_generate` call, answers Procgen Studio's
drawing requests with the matching reference program from
[`procgen_studio/techniques/`](../procgen_studio/techniques), and returns a fixed passing critic
verdict. Everything downstream of the model (Hermes's tool dispatch, the provider, `ctx.llm`,
the sandboxed renderer, STYLE.md resolution, the critic loop bookkeeping, sidecars, re-rolls) ran
for real. Run `bash scripts/e2e.sh` with your own model to regenerate this folder from live
drawings.
