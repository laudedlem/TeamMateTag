"""
Vercel serverless entrypoint. Imports the Flask app from web/server.py.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import os

from flask import Flask

app = Flask(__name__)

try:
    from web.server import app as _real
    app = _real
except Exception:
    import traceback as _tb

    _err = _tb.format_exc()

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def _startup_error(path):
        return (
            f"<h1>Startup Error</h1><pre>{_err}</pre>"
            f"<p>DATABASE_URL set: {'Yes' if os.environ.get('DATABASE_URL') else 'No'}</p>",
            500,
        )
