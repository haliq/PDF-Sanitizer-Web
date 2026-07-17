"""Scan PDF files for interactive or potentially unsafe constructions.

The original project depends on pikepdf. This version keeps pikepdf support when
available, but includes a PyMuPDF fallback so the web application can still run
when pikepdf is not installed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import fitz

from core.logger import logger

try:  # Optional but preferred for object-level inspection.
    import pikepdf  # type: ignore
except ImportError:  # pragma: no cover - depends on runtime environment
    pikepdf = None


UNSAFE_OBJECT_PATTERNS: dict[str, re.Pattern[str]] = {
    "URI": re.compile(r"/URI\b"),
    "JavaScript": re.compile(r"/(?:JavaScript|JS)\b"),
    "OpenAction": re.compile(r"/OpenAction\b"),
    "EmbeddedFile": re.compile(r"/(?:EmbeddedFiles|EmbeddedFile|FileAttachment)\b"),
    "Launch": re.compile(r"/Launch\b"),
    "RemoteGoTo": re.compile(r"/GoToR\b"),
    "SubmitForm": re.compile(r"/SubmitForm\b"),
    "ImportData": re.compile(r"/ImportData\b"),
    "RichMedia": re.compile(r"/RichMedia\b"),
}


def _fmt_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _base_result(path: Path) -> dict[str, Any]:
    size = path.stat().st_size if path.exists() else 0
    return {
        "path": str(path),
        "filename": path.name,
        "size": size,
        "size_fmt": _fmt_size(size),
        "pages": 0,
        "encrypted": False,
        "has_uri": False,
        "has_js": False,
        "has_annot": False,
        "has_embedded": False,
        "has_open_action": False,
        "has_launch": False,
        "has_remote_goto": False,
        "has_submit_form": False,
        "has_import_data": False,
        "has_rich_media": False,
        "safe": True,
        "issues": [],
        "error": None,
        "engine": "pikepdf" if pikepdf is not None else "pymupdf",
    }


def _apply_issue_flags(result: dict[str, Any], found: set[str]) -> None:
    mapping = {
        "URI": "has_uri",
        "JavaScript": "has_js",
        "OpenAction": "has_open_action",
        "EmbeddedFile": "has_embedded",
        "Launch": "has_launch",
        "RemoteGoTo": "has_remote_goto",
        "SubmitForm": "has_submit_form",
        "ImportData": "has_import_data",
        "RichMedia": "has_rich_media",
    }
    ordered = [
        "URI",
        "JavaScript",
        "OpenAction",
        "EmbeddedFile",
        "Launch",
        "RemoteGoTo",
        "SubmitForm",
        "ImportData",
        "RichMedia",
    ]
    for issue in ordered:
        if issue in found:
            result[mapping[issue]] = True
            result["issues"].append(issue)
    result["safe"] = not result["issues"] and not result.get("error")


class PDFScanner:
    """Inspect a PDF and return a JSON-serializable result dictionary."""

    @staticmethod
    def scan_file(file_path: str) -> dict[str, Any]:
        path = Path(file_path)
        result = _base_result(path)

        if not path.is_file():
            result["error"] = "File tidak ditemukan"
            result["safe"] = False
            return result

        try:
            if pikepdf is not None:
                PDFScanner._scan_with_pikepdf(path, result)
            else:
                PDFScanner._scan_with_pymupdf(path, result)
        except Exception as exc:
            result["error"] = f"PDF tidak dapat dibaca: {exc}"
            result["safe"] = False
            logger.exception("Gagal memindai PDF %s", path)
        return result

    @staticmethod
    def _scan_with_pikepdf(path: Path, result: dict[str, Any]) -> None:
        found: set[str] = set()
        try:
            pdf_context = pikepdf.Pdf.open(str(path), suppress_warnings=True)
        except pikepdf.PasswordError as exc:  # type: ignore[union-attr]
            result["encrypted"] = True
            raise ValueError("PDF dilindungi kata sandi") from exc

        with pdf_context as pdf:
            result["pages"] = len(pdf.pages)

            for page in pdf.pages:
                annots = page.get("/Annots")
                if annots:
                    result["has_annot"] = True
                    for annot_ref in annots:
                        try:
                            annot = annot_ref
                            action = annot.get("/A") or annot.get("/AA")
                            if action is not None:
                                PDFScanner._inspect_pike_object(action, found)
                            subtype = str(annot.get("/Subtype", ""))
                            if subtype in ("/FileAttachment", "/RichMedia"):
                                found.add("EmbeddedFile" if subtype == "/FileAttachment" else "RichMedia")
                        except Exception:
                            continue

            PDFScanner._inspect_pike_object(pdf.Root, found)

            # Inspect dictionaries of all indirect objects. This catches actions
            # nested outside page annotations, forms, and name trees.
            for obj in pdf.objects:
                try:
                    PDFScanner._inspect_pike_object(obj, found)
                except Exception:
                    continue

        _apply_issue_flags(result, found)

    @staticmethod
    def _inspect_pike_object(obj: Any, found: set[str]) -> None:
        text = str(obj)
        for issue, pattern in UNSAFE_OBJECT_PATTERNS.items():
            if pattern.search(text):
                found.add(issue)

    @staticmethod
    def _scan_with_pymupdf(path: Path, result: dict[str, Any]) -> None:
        found: set[str] = set()
        doc = fitz.open(str(path))
        try:
            if doc.needs_pass:
                result["encrypted"] = True
                raise ValueError("PDF dilindungi kata sandi")

            result["pages"] = doc.page_count
            for page in doc:
                links = page.get_links()
                if links:
                    result["has_annot"] = True
                for link in links:
                    uri = link.get("uri")
                    if uri:
                        found.add("URI")
                    if link.get("kind") == fitz.LINK_LAUNCH:
                        found.add("Launch")
                    if link.get("file"):
                        found.add("RemoteGoTo")

                # Detect any annotation subtype, including file attachments.
                annot = page.first_annot
                while annot is not None:
                    result["has_annot"] = True
                    subtype = (annot.type[1] or "").lower()
                    if "fileattachment" in subtype:
                        found.add("EmbeddedFile")
                    annot = annot.next

            # Search all PDF object dictionaries. PyMuPDF exposes object source
            # without executing any embedded action.
            for xref in range(1, doc.xref_length()):
                try:
                    obj_text = doc.xref_object(xref, compressed=False)
                except Exception:
                    continue
                for issue, pattern in UNSAFE_OBJECT_PATTERNS.items():
                    if pattern.search(obj_text):
                        found.add(issue)
        finally:
            doc.close()

        _apply_issue_flags(result, found)
