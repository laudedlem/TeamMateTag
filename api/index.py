"""Minimal Vercel Flask entrypoint."""
from flask import Flask
app = Flask(__name__)

@app.route("/")
def hello():
    return "<h1>Teammate Tag is alive</h1><p>Vercel + Flask confirmed working.</p>"
