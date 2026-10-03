"""Vercel entrypoint.

Vercel's Python runtime looks for a `FastAPI` instance named `app`; this module
re-exports the one in `qabuddy/api.py`. It is wired up in `pyproject.toml`:

    [tool.vercel]
    entrypoint = "qabuddy.vercel:app"
"""

from .api import app  # noqa: F401
