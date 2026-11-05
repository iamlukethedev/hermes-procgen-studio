#!/usr/bin/env bash
# End-to-end run: Hermes agent -> built-in image_generate tool -> Procgen Studio -> examples/.
#
# Uses whatever model Hermes is configured with. With E2E_REPLAY=1 it instead points Hermes at
# scripts/replay_llm.py (a scripted local endpoint, for machines without model credentials) and
# restores your model settings afterwards.
#
# Requirements: hermes on PATH, the plugin installed and enabled, and a running gateway
# (`hermes gateway run` in another terminal if no service is installed).
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${E2E_OUT:-$REPO/examples}"
PROJECT="$(mktemp -d -t procgen-e2e-XXXX)"
REPLAY_PORT="${REPLAY_PORT:-8765}"

say() { printf '\n\033[1;36m== %s\033[0m\n' "$*"; }

restore=()
if [[ "${E2E_REPLAY:-0}" == "1" ]]; then
  say "Starting the replay endpoint on :$REPLAY_PORT"
  python3 "$REPO/scripts/replay_llm.py" --port "$REPLAY_PORT" >/tmp/procgen-replay.log 2>&1 &
  REPLAY_PID=$!
  trap 'kill $REPLAY_PID 2>/dev/null || true' EXIT
  sleep 1
  for key in model.provider model.base_url model.default; do
    restore+=("$key=$(hermes config get "$key" 2>/dev/null || true)")
  done
  hermes config set model.provider custom >/dev/null
  hermes config set model.base_url "http://127.0.0.1:$REPLAY_PORT/v1" >/dev/null
  hermes config set model.default replay >/dev/null
fi

hermes config set image_gen.provider procgen-studio >/dev/null

cp "$REPO/examples/STYLE.md" "$PROJECT/STYLE.md" 2>/dev/null || cat >"$PROJECT/STYLE.md" <<'EOF'
# STYLE.md - shared art contract
palette: ember
outline: 10px ink, round joins
lighting: top-left key light, soft rim on lower-right
shading: two-tone cel + one gradient, one specular highlight per object
background: rounded tile, radial vignette
EOF
sed -i.bak 's/^palette: .*/palette: ember/' "$PROJECT/STYLE.md" && rm -f "$PROJECT/STYLE.md.bak"
mkdir -p "$OUT"

collect() {  # collect <name>: copy the newest render's PNG, source and metadata into examples/
  local name="$1" line png stem dir
  line="$(cd "$PROJECT" && hermes procgen show last | grep '^  output:' | sed 's/^  output:  //')"
  png="$line"
  stem="${png%.png}"
  dir="$(dirname "$png")"
  cp "$png" "$OUT/$name.png"
  for side in "$stem".py "$stem".svg "$stem".pixel.json; do
    [[ -f "$side" ]] && cp "$side" "$OUT/$name.${side#"$stem".}"
  done
  cp "$stem.procgen.json" "$OUT/$name.procgen.json"
  (cd "$PROJECT" && hermes procgen show last) >"$OUT/$name.log.txt"
  echo "  -> examples/$name.png"
}

ask() {  # ask <name> <prompt>
  say "$1: $2"
  (cd "$PROJECT" && hermes -z "$2")
  collect "$1"
}

ICONS="Generate a set of 4 game icons on rounded tiles: a sword, a health potion, a shield and a key. [set=dungeon-loot]"

ask 01-icons-ember "$ICONS"

say "One-line STYLE.md change: palette ember -> frost"
sed -i.bak 's/^palette: ember$/palette: frost/' "$PROJECT/STYLE.md" && rm -f "$PROJECT/STYLE.md.bak"
grep -n '^palette' "$PROJECT/STYLE.md"
ask 02-icons-frost "$ICONS"

ask 03-scene "A wide flat-illustration hero scene: a cabin in a moonlit pine forest at night, warm windows, a path leading to the door, layered hills and fog. [style=flat-illustration]"
ask 04-logo "A logo and wordmark for a developer tools startup called forgeline, tagline 'SHIP AGENTS, NOT GLUE': a hexagon mark with a terminal prompt glyph. [style=brand-minimal]"
ask 05-monster "A low-poly dark fantasy monster called THE HOLLOW WARDEN: hulking horned golem with glowing eyes and an ember crack in its chest, full body. [style=dark-fantasy-lowpoly]"

say "Local re-roll of the scene with a new seed (no model call)"
(cd "$PROJECT" && hermes procgen reroll "$(grep -o 'pg-[0-9a-f]\{8\}' "$OUT/03-scene.procgen.json" | head -1)" --seed 7)
collect 06-scene-reroll-seed7

if [[ "${#restore[@]}" -gt 0 ]]; then
  say "Restoring your model settings"
  for kv in "${restore[@]}"; do
    key="${kv%%=*}"; value="${kv#*=}"
    if [[ -n "$value" ]]; then hermes config set "$key" "$value" >/dev/null; fi
  done
fi

say "Done. Outputs in $OUT (project dir: $PROJECT)"
