# Local-model check (qwen2.5-coder 7B on CPU)

A pipeline check with a genuine model, not a quality showcase. Hermes's main model was the replay
endpoint (to issue the tool call); the plugin's **Procgen Studio drawing** and **Procgen Studio
critic** auxiliary slots were pinned to `qwen2.5-coder:7b` served by a local Ollama on a 4-core
CPU, with `critic: structure`, `max_redraws: 1` and Hermes's tool deadline raised
(`timeouts.tools.sequential_call: 3600`, `time_budget: 3300`).

Prompt: `A single app icon: a paper rocket ship launching diagonally on a rounded square tile, with a
soft glow and a small star. [style=brand-minimal] [set=live-local]`

| Call | Seconds | Tokens |
|---|---|---|
| draw | 340 | 6771 |
| critic (structure) | 91 | 2648 |
| redraw 1 | 335 | 8095 |
| critic (structure) | 65 | 2648 |

What it showed:

- The whole path works with a real model: `ctx.llm` routed through the plugin's own auxiliary
  slots, sandboxed render, critic, redraw, sidecars, delivery back to the agent
  (`rocket-qwen2.5-coder-7b.png`, `.py`, `.log.txt`).
- A 7B model's drawing is weak, and the critic scored it 6/10 (below the pass score), so the
  render was marked as not passing; the best attempt was still delivered. Its redraw was byte for
  byte the same program, which is why the loop now stops as soon as a model repeats itself.
- Before the copy guard, the same model pasted the logo technique reference (including its
  "forgeline" wordmark) instead of drawing a rocket (`before-copy-guard-attempt1.py`,
  `before-copy-guard.log.txt`), and as a structure critic it rubber-stamped that with 8/10.
  Procgen Studio now rejects a drawing that carries a reference's text or is a near copy of a
  reference the request did not ask for, without spending a model call, and asks for a redraw.
- At about 3.5 tokens/s a single drawing call takes more than five minutes, past Hermes's
  default 420 s tool deadline; `time_budget` keeps runs inside that deadline by skipping redraws.
