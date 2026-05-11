"""Vercel entrypoint for the Teammate Tag Flask app."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from web.server import app  # noqa: E402,F401
