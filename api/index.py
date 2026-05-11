"""
Vercel serverless entrypoint. Lazily loads the Flask app from web/server.py
on the first request so that import errors show a diagnostic page instead
of Vercel's generic 404.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


class _WSGIApp:
    """Lazy-loading WSGI wrapper. Avoids any third-party imports at module
    level so Vercel's Python runtime can always find `app`."""

    def __init__(self):
        self._app = None

    def _load(self):
        if self._app is not None:
            return
        try:
            from web.server import app
            self._app = app
        except Exception:
            import traceback
            tb = traceback.format_exc()
            from flask import Flask
            fallback = Flask(__name__)

            @fallback.route("/", defaults={"path": ""})
            @fallback.route("/<path:path>")
            def err(path):
                return (
                    "<h1>Startup Error</h1>"
                    f"<pre>{tb}</pre>"
                    f"<p>DATABASE_URL set: "
                    f"{'Yes' if os.environ.get('DATABASE_URL') else 'No'}</p>",
                    500,
                )
            self._app = fallback

    def __call__(self, environ, start_response):
        self._load()
        return self._app(environ, start_response)


app = _WSGIApp()
