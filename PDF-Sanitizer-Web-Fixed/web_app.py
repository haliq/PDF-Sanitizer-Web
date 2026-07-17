"""Run the PDF Sanitizer web server.

Usage:
    python web_app.py
"""
from __future__ import annotations

import os

import uvicorn

from web.server import app  # noqa: F401


if __name__ == "__main__":
    uvicorn.run(
        "web.server:app",
        host=os.getenv("PDF_SANITIZER_HOST", "0.0.0.0"),
        port=int(os.getenv("PDF_SANITIZER_PORT", "8000")),
        reload=os.getenv("PDF_SANITIZER_RELOAD", "false").lower() == "true",
        log_level=os.getenv("PDF_SANITIZER_UVICORN_LOG_LEVEL", "info"),
    )
