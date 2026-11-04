"""Compatibility shim for Telegram handlers.

Runtime handlers live in ``adapters.telegram.handlers``.  ``bot.py`` still
imports ``router`` from this root module, so this file keeps the old public
entry point while the Telegram layer is split into smaller routers.
"""

from adapters.telegram.handlers import router

__all__ = ["router"]
