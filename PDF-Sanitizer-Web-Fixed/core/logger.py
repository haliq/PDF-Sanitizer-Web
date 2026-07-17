from __future__ import annotations

import logging
import os


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("pdf_sanitizer")
    if logger.handlers:
        return logger

    level_name = os.getenv("PDF_SANITIZER_LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    return logger


logger = _build_logger()
