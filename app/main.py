"""FastAPI service: upload audio, get a transcript with timestamps per segment."""
import logging
import os
import shutil
import uuid

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse

from .audio import AudioError, probe_audio
from .formats import to_srt, to_txt, to_vtt
from .jobs import JobStore
from .pipeline import run_pipeline
from .transcriber import get_transcriber

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

ALLOWED_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".webm", ".mp4", ".mov"}
MAX_BYTES = int(os.getenv("MAX_UPLOAD_MB", "500")) * 1024 * 1024
SYNC_LIMIT_SECONDS = float(os.getenv("SYNC_LIMIT_SECONDS", "300"))  # longer files go async
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/tmp/stt_uploads")

app = FastAPI(title="Transcription Pipeline", version="1.0.0")
_engine = None
jobs = JobStore(max_workers=int(os.getenv("JOB_WORKERS", "2")))


def engine():
    global _engine
    if _engine is None:  # load the model once per process, not per request
        _engine = get_transcriber()
    return _engine


def _save_upload(file: UploadFile) -> str:
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(415, f"Unsupported file type '{ext}'. Allowed: {sorted(ALLOWED_EXT)}")
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}{ext}")
    size = 0
    with open(path, "wb") as out:  # stream in 1 MB pieces, never the whole file in memory
        while chunk := file.file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_BYTES:
                out.close()
                os.remove(path)
                raise HTTPException(413, "File too large")
            out.write(chunk)
    return path


@app.get("/health")
def health():
    return {"status": "ok", "engine": os.getenv("STT_ENGINE", "faster-whisper")}


@app.post("/v1/transcriptions")
def create_transcription(
    file: UploadFile = File(...),
    language: str | None = Query(None, description="ISO code like 'en'. Empty = auto detect"),
    word_timestamps: bool = False,
    sync: bool | None = Query(None, description="Force sync or async. Default: by duration"),
):
    path = _save_upload(file)
    try:
        info = probe_audio(path)  # real format check, not just the extension
    except AudioError as e:
        os.remove(path)
        raise HTTPException(400, str(e))

    run_sync = sync if sync is not None else info.duration <= SYNC_LIMIT_SECONDS
    if run_sync:
        try:
            return run_pipeline(path, engine(), language, word_timestamps)
        except AudioError as e:
            raise HTTPException(400, str(e))
        finally:
            os.remove(path)

    job = jobs.create(file.filename or "upload")

    def work(p, lang, words, progress):
        try:
            return run_pipeline(p, engine(), lang, words, on_progress=progress)
        finally:
            if os.path.exists(p):
                os.remove(p)

    jobs.submit(job["id"], work, path, language, word_timestamps)
    return {"job_id": job["id"], "status": "queued", "duration": round(info.duration, 2),
            "status_url": f"/v1/transcriptions/{job['id']}"}


@app.get("/v1/transcriptions/{job_id}")
def get_transcription(job_id: str, format: str = Query("json", pattern="^(json|srt|vtt|txt)$")):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if format == "json" or job["status"] != "completed":
        return job
    segs = job["result"]["segments"]
    body = {"srt": to_srt, "vtt": to_vtt, "txt": to_txt}[format](segs)
    return PlainTextResponse(body, media_type="text/plain; charset=utf-8")
