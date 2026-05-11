"""Top-level Vercel Flask entrypoint.

Vercel's Python runtime looks for a WSGI/ASGI app named `app` in common
top-level files such as `app.py`, `index.py`, or `server.py`. Exposing the
Flask app here keeps deployment simple and avoids relying on a rewrite from
`/` into the `api/` directory.
"""
from __future__ import annotations

from web.server import app
