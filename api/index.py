"""Minimal Vercel Flask test."""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from flask import Flask
app = Flask(__name__)

@app.route("/")
def hello():
    return "<h1>Teammate Tag app is alive</h1><p>Flask + Vercel works.</p>"
