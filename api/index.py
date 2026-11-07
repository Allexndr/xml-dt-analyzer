"""Vercel entry point: the FastAPI application as a Python serverless function.

All ``/api/*`` requests are rewritten here (see vercel.json). Reference data (schemas, knowledge base,
derived artefacts) is bundled with the function via ``includeFiles``; persistent state lives in
Postgres (``DATABASE_URL`` from the Neon integration).
"""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
os.environ.setdefault("XMLDT_DATA", str(ROOT / "data"))
os.environ.setdefault("XMLDT_VAR", "/tmp/xmldt")       # scratch only; nothing persistent is written here
os.environ.setdefault("XMLDT_RELAY_DISABLED", "1")     # no WebSockets on Vercel functions
os.environ.setdefault("XMLDT_COOKIE_SECURE", "1")      # Vercel serves HTTPS only
os.environ.setdefault("XMLDT_MAX_UPLOAD", str(4 * 1024 * 1024))  # function request bodies are limited to ~4.5 MB

from xmldt.api.app import app  # noqa: E402,F401
