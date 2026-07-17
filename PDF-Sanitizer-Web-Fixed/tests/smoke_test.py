from __future__ import annotations

import tempfile
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import fitz
from fastapi.testclient import TestClient

from core.renderer import PDFRenderer
from core.scanner import PDFScanner
from web.server import app


def make_pdf(path: Path, with_uri: bool = False) -> None:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 100), "PDF Sanitizer smoke test", fontsize=18)
    if with_uri:
        page.insert_link({
            "kind": fitz.LINK_URI,
            "from": fitz.Rect(70, 120, 260, 150),
            "uri": "https://example.com",
        })
    doc.save(path)
    doc.close()


def run() -> None:
    with tempfile.TemporaryDirectory() as temp:
        temp_dir = Path(temp)
        unsafe_pdf = temp_dir / "unsafe.pdf"
        output_pdf = temp_dir / "clean.pdf"
        make_pdf(unsafe_pdf, with_uri=True)

        initial = PDFScanner.scan_file(str(unsafe_pdf))
        assert initial["has_uri"] is True, initial
        assert initial["safe"] is False, initial

        assert PDFRenderer.render_and_rebuild(str(unsafe_pdf), str(output_pdf), 100)
        final = PDFScanner.scan_file(str(output_pdf))
        assert final["safe"] is True, final
        assert final["has_uri"] is False, final

        client = TestClient(app)
        health = client.get("/api/health")
        assert health.status_code == 200, health.text
        assert health.json()["ok"] is True

        home = client.get("/")
        assert home.status_code == 200, home.text
        assert "PDF Sanitizer" in home.text

    print("SMOKE TEST OK")


if __name__ == "__main__":
    run()
