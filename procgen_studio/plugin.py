"""Hermes entry point: one engine shared by the image backend, video backend, /procgen and the CLI."""

from __future__ import annotations

import logging
from pathlib import Path

from . import commands
from .engine import Engine, JsonState, Settings
from .llm import CRITIC_TASK, DRAW_TASK, Llm

logger = logging.getLogger("procgen_studio")

SKILLS_DIR = Path(__file__).resolve().parent.parent / "skills"


def _register_tasks(ctx):
    """Own auxiliary slots so users can pin a strong coder for drawing and a vision model for review."""
    draw_task = critic_task = None
    register = getattr(ctx, "register_auxiliary_task", None)
    if register is None:
        return None, None
    try:
        register(DRAW_TASK, display_name="Procgen Studio drawing",
                 description="Writes the SVG/Python drawing programs (defaults to your main model).",
                 defaults={"timeout": 300})
        draw_task = DRAW_TASK
    except Exception as exc:
        logger.warning("procgen-studio: could not register auxiliary task %s: %s", DRAW_TASK, exc)
    try:
        register(CRITIC_TASK, display_name="Procgen Studio critic",
                 description="Reviews each render against the prompt and STYLE.md (follows the vision slot).",
                 inherit_from="vision", defaults={"timeout": 180})
        critic_task = CRITIC_TASK
    except Exception as exc:
        logger.warning("procgen-studio: could not register auxiliary task %s: %s", CRITIC_TASK, exc)
    return draw_task, critic_task


def build_engine(ctx) -> Engine:
    draw_task, critic_task = _register_tasks(ctx)
    getter = getattr(ctx, "get_config", None)

    def settings() -> Settings:
        return Settings.load(getter)

    def llm_factory(s: Settings) -> Llm:
        return Llm(ctx.llm, draw_task=draw_task, critic_task=critic_task, max_tokens=s.max_tokens,
                   timeout=float(s.llm_timeout))

    try:
        state = ctx.state
    except Exception:
        state = JsonState()
    return Engine(llm_factory=llm_factory, settings=settings, state=state)


def register(ctx) -> None:
    from .provider import ProcgenImageProvider, make_video_provider

    engine = build_engine(ctx)
    ctx.register_image_gen_provider(ProcgenImageProvider(engine))
    if hasattr(ctx, "register_video_gen_provider"):
        try:
            ctx.register_video_gen_provider(make_video_provider(engine))
        except ImportError as exc:
            logger.info("procgen-studio: video backend unavailable on this Hermes build: %s", exc)
    ctx.register_command("procgen", lambda raw="": commands.run_text(engine, raw),
                         description="Procgen Studio: re-roll seeds, switch styles, restyle or regenerate sets",
                         args_hint="<status|styles|style|reroll|restyle|regen|edit|list|show|source|gallery>")
    ctx.register_cli_command(name="procgen", help="Procgen Studio: styles, re-rolls, restyles, local renders",
                             setup_fn=commands.cli_setup, handler_fn=commands.cli_handler_factory(lambda: engine),
                             description=commands.HELP)
    skill = SKILLS_DIR / "procgen-art-direction" / "SKILL.md"
    if skill.is_file() and hasattr(ctx, "register_skill"):
        try:
            ctx.register_skill("procgen-art-direction", skill)
        except Exception as exc:
            logger.info("procgen-studio: skill registration skipped: %s", exc)
