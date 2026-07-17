"""FastAPI server for the corrected PDF Sanitizer web application."""
from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from core.logger import logger
from core.renderer import PDFRenderer
from core.scanner import PDFScanner

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(
    os.getenv(
        "PDF_SANITIZER_DATA_DIR",
        str(Path(tempfile.gettempdir()) / "pdf_sanitizer_web"),
    )
).resolve()
UPLOAD_DIR = DATA_DIR / "sessions"
OUTPUT_DIR = DATA_DIR / "jobs"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

MAX_FILE_SIZE = int(os.getenv("PDF_SANITIZER_MAX_FILE_SIZE", str(50 * 1024 * 1024)))
MAX_FILES_PER_SESSION = int(os.getenv("PDF_SANITIZER_MAX_FILES", "100"))
SESSION_TTL_SECONDS = int(os.getenv("PDF_SANITIZER_SESSION_TTL", str(6 * 60 * 60)))
MAX_WORKERS = max(1, min(8, int(os.getenv("PDF_SANITIZER_WORKERS", "4"))))
CHUNK_SIZE = 1024 * 1024
_executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="pdf-sanitizer")

sessions: dict[str, dict[str, Any]] = {}
jobs: dict[str, dict[str, Any]] = {}
ws_connections: dict[str, set[WebSocket]] = {}
state_lock = asyncio.Lock()


class ScanRequest(BaseModel):
    sid: str
    fids: list[str] | None = None


class SanitizeRequest(BaseModel):
    sid: str
    fids: list[str] = Field(default_factory=list)
    dpi: int = Field(default=150, ge=72, le=300)


def safe_filename(filename: str | None) -> str:
    name = Path(filename or "document.pdf").name
    name = re.sub(r"[^A-Za-z0-9._()\- ]+", "_", name).strip(" .")
    if not name:
        name = "document.pdf"
    if not name.lower().endswith(".pdf"):
        name += ".pdf"
    return name[:180]


def public_job(job_id: str, job: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": job_id,
        "status": job["status"],
        "total": job["total"],
        "done": job["done"],
        "success": job["success"],
        "failed": job["failed"],
        "results": job["results"],
        "created_at": job["created_at"],
        "download_ready": bool(job.get("output_zip")),
        "error": job.get("error"),
    }


def _session_path(sid: str) -> Path:
    return UPLOAD_DIR / sid


def _job_path(job_id: str) -> Path:
    return OUTPUT_DIR / job_id


def _is_pdf_header(header: bytes) -> bool:
    return header.lstrip().startswith(b"%PDF-")


async def _save_upload(upload: UploadFile, destination: Path) -> int:
    size = 0
    first_chunk = True
    with destination.open("wb") as output:
        while True:
            chunk = await upload.read(CHUNK_SIZE)
            if not chunk:
                break
            if first_chunk:
                first_chunk = False
                if not _is_pdf_header(chunk[:1024]):
                    raise HTTPException(status_code=415, detail=f"{upload.filename}: bukan file PDF yang valid")
            size += len(chunk)
            if size > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail=f"{upload.filename}: ukuran melebihi {MAX_FILE_SIZE // (1024 * 1024)} MB",
                )
            output.write(chunk)
    if size == 0:
        raise HTTPException(status_code=400, detail=f"{upload.filename}: file kosong")
    return size


async def _broadcast(job_id: str, message: dict[str, Any]) -> None:
    dead: list[WebSocket] = []
    for websocket in list(ws_connections.get(job_id, set())):
        try:
            await websocket.send_json(message)
        except Exception:
            dead.append(websocket)
    for websocket in dead:
        ws_connections.get(job_id, set()).discard(websocket)


async def _cleanup_expired() -> None:
    while True:
        await asyncio.sleep(900)
        cutoff = time.time() - SESSION_TTL_SECONDS
        async with state_lock:
            expired_sessions = [sid for sid, item in sessions.items() if item["updated_at"] < cutoff]
            expired_jobs = [job_id for job_id, item in jobs.items() if item["created_at"] < cutoff]
            for sid in expired_sessions:
                sessions.pop(sid, None)
                shutil.rmtree(_session_path(sid), ignore_errors=True)
            for job_id in expired_jobs:
                jobs.pop(job_id, None)
                ws_connections.pop(job_id, None)
                shutil.rmtree(_job_path(job_id), ignore_errors=True)


@asynccontextmanager
async def lifespan(_: FastAPI):
    cleanup_task = asyncio.create_task(_cleanup_expired())
    try:
        yield
    finally:
        cleanup_task.cancel()
        _executor.shutdown(wait=False, cancel_futures=True)


app = FastAPI(
    title="PDF Sanitizer",
    version="2.0.0",
    description="Sanitasi PDF untuk menghapus URI, JavaScript, OpenAction, dan objek interaktif.",
    lifespan=lifespan,
)


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {
        "ok": True,
        "version": app.version,
        "max_file_mb": MAX_FILE_SIZE // (1024 * 1024),
        "max_files": MAX_FILES_PER_SESSION,
        "workers": MAX_WORKERS,
    }


@app.post("/api/upload")
async def upload_files(
    files: list[UploadFile] = File(...),
    sid: str = Form(default=""),
) -> dict[str, Any]:
    if not files:
        raise HTTPException(status_code=400, detail="Tidak ada file yang dikirim")

    sid = sid if sid in sessions else uuid.uuid4().hex
    session_dir = _session_path(sid)
    session_dir.mkdir(parents=True, exist_ok=True)

    async with state_lock:
        session = sessions.setdefault(sid, {"files": {}, "updated_at": time.time()})
        if len(session["files"]) + len(files) > MAX_FILES_PER_SESSION:
            raise HTTPException(status_code=400, detail=f"Maksimal {MAX_FILES_PER_SESSION} file per sesi")

    uploaded: list[dict[str, Any]] = []
    for upload in files:
        filename = safe_filename(upload.filename)
        if upload.content_type not in (None, "", "application/pdf", "application/octet-stream"):
            raise HTTPException(status_code=415, detail=f"{filename}: tipe file tidak diizinkan")

        fid = uuid.uuid4().hex
        destination = session_dir / f"{fid}_{filename}"
        try:
            size = await _save_upload(upload, destination)
        except Exception:
            destination.unlink(missing_ok=True)
            raise
        finally:
            await upload.close()

        async with state_lock:
            session["files"][fid] = {
                "name": filename,
                "path": str(destination),
                "size": size,
                "scan": None,
            }
            session["updated_at"] = time.time()
        uploaded.append({"fid": fid, "name": filename, "size": size})

    return {"sid": sid, "uploaded": uploaded}


@app.post("/api/scan")
async def scan_files(payload: ScanRequest) -> dict[str, Any]:
    session = sessions.get(payload.sid)
    if not session:
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan atau sudah kedaluwarsa")

    requested = payload.fids or list(session["files"].keys())
    target = [fid for fid in requested if fid in session["files"]]
    if not target:
        raise HTTPException(status_code=400, detail="Tidak ada file yang dapat dipindai")

    loop = asyncio.get_running_loop()

    async def scan_one(fid: str) -> dict[str, Any]:
        info = session["files"][fid]
        result = await loop.run_in_executor(_executor, PDFScanner.scan_file, info["path"])
        info["scan"] = result
        return {
            "fid": fid,
            "name": info["name"],
            "size_fmt": result["size_fmt"],
            "pages": result["pages"],
            "encrypted": result["encrypted"],
            "has_uri": result["has_uri"],
            "has_js": result["has_js"],
            "has_annot": result["has_annot"],
            "has_embedded": result["has_embedded"],
            "has_open_action": result["has_open_action"],
            "safe": result["safe"],
            "issues": result["issues"],
            "error": result["error"],
            "engine": result["engine"],
        }

    results = await asyncio.gather(*(scan_one(fid) for fid in target))
    session["updated_at"] = time.time()
    return {"sid": payload.sid, "results": results}


@app.post("/api/sanitize")
async def start_sanitize(payload: SanitizeRequest) -> dict[str, str]:
    session = sessions.get(payload.sid)
    if not session:
        raise HTTPException(status_code=404, detail="Sesi tidak ditemukan atau sudah kedaluwarsa")

    requested = payload.fids or list(session["files"].keys())
    target = [fid for fid in requested if fid in session["files"]]
    if not target:
        raise HTTPException(status_code=400, detail="Tidak ada file yang dapat diproses")

    job_id = uuid.uuid4().hex
    job_dir = _job_path(job_id)
    output_files = job_dir / "files"
    output_files.mkdir(parents=True, exist_ok=True)

    jobs[job_id] = {
        "status": "queued",
        "total": len(target),
        "done": 0,
        "success": 0,
        "failed": 0,
        "results": [],
        "output_zip": None,
        "created_at": time.time(),
        "error": None,
    }
    ws_connections[job_id] = set()
    asyncio.create_task(_run_sanitize(job_id, payload.sid, target, payload.dpi, output_files))
    return {"job_id": job_id}


async def _run_sanitize(job_id: str, sid: str, fids: list[str], dpi: int, output_files: Path) -> None:
    job = jobs[job_id]
    session = sessions.get(sid)
    if not session:
        job.update(status="failed", error="Sesi sudah tidak tersedia")
        await _broadcast(job_id, {"type": "failed", "message": job["error"]})
        return

    job["status"] = "running"
    loop = asyncio.get_running_loop()
    started = time.monotonic()

    for index, fid in enumerate(fids, start=1):
        info = session["files"][fid]
        output_name = info["name"]
        output_path = output_files / output_name
        if output_path.exists():
            output_path = output_files / f"{Path(output_name).stem}_{fid[:6]}.pdf"

        success = await loop.run_in_executor(
            _executor,
            PDFRenderer.render_and_rebuild,
            info["path"],
            str(output_path),
            dpi,
        )

        # Verify output again. A successful render should produce a clean PDF.
        verification = None
        if success:
            verification = await loop.run_in_executor(_executor, PDFScanner.scan_file, str(output_path))
            success = bool(verification.get("safe") and not verification.get("error"))
            if not success:
                output_path.unlink(missing_ok=True)

        issues = (info.get("scan") or {}).get("issues", [])
        entry = {
            "fid": fid,
            "name": info["name"],
            "status": "success" if success else "failed",
            "message": (
                "Berhasil dibersihkan" + (f": {', '.join(issues)}" if issues else "")
                if success
                else "Gagal membuat atau memverifikasi PDF bersih"
            ),
            "out_name": output_path.name if success else None,
        }
        job["results"].append(entry)
        job["done"] = index
        job["success" if success else "failed"] += 1

        elapsed = max(time.monotonic() - started, 0.001)
        rate = index / elapsed
        remaining = (job["total"] - index) / rate if rate else 0
        await _broadcast(
            job_id,
            {
                "type": "progress",
                "done": index,
                "total": job["total"],
                "pct": round(index / job["total"] * 100),
                "eta_seconds": round(remaining),
                "entry": entry,
            },
        )

    successful_files = [item for item in output_files.iterdir() if item.is_file()]
    if successful_files:
        zip_path = _job_path(job_id) / f"sanitized_{job_id[:8]}.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for file_path in sorted(successful_files):
                    archive.write(file_path, arcname=file_path.name)
            job["output_zip"] = str(zip_path)
            job["status"] = "done"
        except Exception as exc:
            logger.exception("Gagal membuat ZIP job %s", job_id)
            job.update(status="failed", error=f"Gagal membuat ZIP: {exc}")
    else:
        job.update(status="failed", error="Semua file gagal diproses")

    await _broadcast(job_id, {"type": "done", **public_job(job_id, job)})


@app.websocket("/ws/{job_id}")
async def websocket_progress(websocket: WebSocket, job_id: str) -> None:
    await websocket.accept()
    if job_id not in jobs:
        await websocket.send_json({"type": "failed", "message": "Job tidak ditemukan"})
        await websocket.close(code=1008)
        return

    ws_connections.setdefault(job_id, set()).add(websocket)
    await websocket.send_json({"type": "snapshot", **public_job(job_id, jobs[job_id])})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_connections.get(job_id, set()).discard(websocket)
    except Exception:
        ws_connections.get(job_id, set()).discard(websocket)


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan")
    return public_job(job_id, job)


@app.get("/api/download/{job_id}")
async def download_result(job_id: str) -> FileResponse:
    job = jobs.get(job_id)
    if not job or not job.get("output_zip"):
        raise HTTPException(status_code=404, detail="File hasil belum tersedia")
    zip_path = Path(job["output_zip"])
    if not zip_path.is_file():
        raise HTTPException(status_code=404, detail="File hasil sudah tidak tersedia")
    return FileResponse(
        path=str(zip_path),
        filename=zip_path.name,
        media_type="application/zip",
    )


@app.delete("/api/session/{sid}")
async def delete_session(sid: str) -> dict[str, bool]:
    sessions.pop(sid, None)
    shutil.rmtree(_session_path(sid), ignore_errors=True)
    return {"ok": True}


app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="frontend")
