"""Procgen Studio plugin entry point for Hermes Agent.

The implementation lives in the ``procgen_studio`` subpackage; this module only
exposes ``register(ctx)`` where the plugin loader expects it.
"""

from .procgen_studio.plugin import register  # noqa: F401
