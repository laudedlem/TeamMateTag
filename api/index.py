"""
Vercel serverless entrypoint. Catches startup errors so they show in the
browser instead of a blank 404.
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from web.server import app
except Exception:
    from flask import Flask

    app = Flask(__name__)

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def startup_error(path):
        tb = traceback.format_exc()
        return (
            f"<h1>Startup Error</h1><pre>{tb}</pre>"
            f"<p>DATABASE_URL set: {'Yes' if __import__('os').environ.get('DATABASE_URL') else 'No'}</p>",
            500,
        )
