"""
Vercel serverless entrypoint. Imports the Flask app from web/server.py
so Vercel's @vercel/python builder exposes every route through one
function. vercel.json rewrites all paths to /api/index.

Local dev still uses `python web/server.py` directly; this file is
only invoked by Vercel.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Vercel discovers the WSGI `app` and serves it.
from web.server import app  # noqa: E402,F401
