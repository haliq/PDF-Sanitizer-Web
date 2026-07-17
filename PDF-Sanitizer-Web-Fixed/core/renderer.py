"""Render each PDF page to a flat image and rebuild a clean PDF."""
from __future__ import annotations

from pathlib import Path

import fitz

from core.logger import logger


class PDFRenderer:
    """Flatten a PDF similarly to 'Print as PDF'."""

    MIN_DPI = 72
    MAX_DPI = 300

    @staticmethod
    def render_and_rebuild(input_path: str, output_path: str, dpi: int = 150) -> bool:
        dpi = max(PDFRenderer.MIN_DPI, min(PDFRenderer.MAX_DPI, int(dpi)))
        source_path = Path(input_path)
        target_path = Path(output_path)
        temporary_path = target_path.with_suffix(target_path.suffix + ".part")

        try:
            source = fitz.open(str(source_path))
        except Exception as exc:
            logger.error("Gagal membuka %s: %s", source_path, exc)
            return False

        rebuilt = fitz.open()
        try:
            if source.needs_pass:
                raise ValueError("PDF dilindungi kata sandi")
            if source.page_count == 0:
                raise ValueError("PDF tidak memiliki halaman")

            matrix = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            for page in source:
                pixmap = page.get_pixmap(matrix=matrix, alpha=False, colorspace=fitz.csRGB)
                image_bytes = pixmap.tobytes("png")

                target_page = rebuilt.new_page(
                    width=page.rect.width,
                    height=page.rect.height,
                )
                target_page.insert_image(target_page.rect, stream=image_bytes, keep_proportion=False)

            target_path.parent.mkdir(parents=True, exist_ok=True)
            rebuilt.set_metadata({})
            rebuilt.save(
                str(temporary_path),
                garbage=4,
                deflate=True,
                clean=True,
                pretty=False,
            )
            temporary_path.replace(target_path)
            return True
        except Exception as exc:
            logger.exception("Gagal membersihkan %s: %s", source_path, exc)
            temporary_path.unlink(missing_ok=True)
            target_path.unlink(missing_ok=True)
            return False
        finally:
            rebuilt.close()
            source.close()
