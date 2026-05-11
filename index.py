"""Simple Vercel Flask entrypoint.

Vercel's Flask/Python deployment can load a top-level `index.py` that exposes
an `app` object. Keep this file tiny and let `web.server` own the real app.
"""
from __future__ import annotations

from web.server import app
