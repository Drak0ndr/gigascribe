"""Local FastAPI service for audio transcription. Not designed for public deployment."""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, Request, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel

from backend.transcription import MODELS, transcribe_audio, unload_model, ffmpeg_executable, device_info
from backend import model_store, vad_store, model_inventory

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"
MAX_FILE_BYTES = 200 * 1024 * 1024
ALLOWED_EXTENSIONS = {
    ".mp3", ".wav", ".m4a", ".mp4", ".flac", ".ogg", ".opus",
    ".webm", ".aac", ".wma", ".mov", ".mkv",
}

app = FastAPI(title="GigaScribe", version="1.3.6")
app.state.desktop_token = None


def require_local_token(request: Request) -> None:
    """Require unpredictable token for mutating actions when started by desktop GUI.

    Blocks cross-origin drive-by requests against the loopback server. In dev mode
    token is absent to keep HTTP API tests and CLI development usable.
    """
    expected = app.state.desktop_token
    if expected and not secrets.compare_digest(request.headers.get('X-GigaScribe-Token', ''), expected):
        raise HTTPException(403, 'Доступ разрешён только из окна GigaScribe')

_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gigaam")
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_model_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gigascribe-download")
_operation_lock = threading.RLock()


def _update(job_id: str, **changes) -> None:
    with _jobs_lock:
        if job_id in _jobs:
            _jobs[job_id].update(changes)


def _process(job_id: str, path: Path, temp_dir: str, model: str, mode: str, device: str) -> None:
    try:
        _update(job_id, status="processing", phase="preparing", progress=2)

        def progress(phase: str, percent: int, detail: str) -> None:
            _update(job_id, phase=phase, progress=percent, detail=detail)

        result = transcribe_audio(path, Path(temp_dir), model, progress, mode=mode, device=device)
        _update(job_id, status="completed", phase="done", detail="Готово", progress=100,
                result=result)
    except Exception as exc:
        _update(job_id, status="failed", phase="error", detail="Ошибка",
                error=str(exc) or type(exc).__name__)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "ffmpeg": ffmpeg_executable() is not None,
        "models": MODELS,
        "device": device_info(),
        "max_file_mb": MAX_FILE_BYTES // 1024 // 1024,
    }



@app.get("/api/models")
def models_status() -> dict:
    # Return an in-memory snapshot immediately; never scan disks in this route.
    return model_inventory.get_status()


class VadRequest(BaseModel):
    token: str


@app.post('/api/vad/download', status_code=202)
def request_vad_download(data: VadRequest, _: None = Depends(require_local_token)) -> dict:
    if not data.token.strip():
        raise HTTPException(422, 'Требуется токен Hugging Face с доступом к pyannote/segmentation-3.0')
    if vad_store.begin_download():
        _model_executor.submit(vad_store.download, data.token.strip())
    return {'status': 'downloading' if not vad_store.installed() else 'installed'}


@app.delete('/api/vad')
def remove_vad(_: None = Depends(require_local_token)) -> dict:
    with _operation_lock:
        with _jobs_lock:
            if any(job.get('mode') == 'vad' and job['status'] in ('queued', 'processing') for job in _jobs.values()):
                raise HTTPException(409, 'VAD используется в текущей расшифровке')
        try:
            vad_store.delete()
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {'status': 'not_installed'}


@app.post("/api/models/{choice}/download", status_code=202)
def request_model_download(choice: str, _: None = Depends(require_local_token)) -> dict:
    if choice not in MODELS:
        raise HTTPException(404, "Неизвестная модель")
    if model_store.installed(choice):
        return {"status": "installed"}
    if model_store.begin_download(choice):
        _model_executor.submit(model_store.download_model, choice)
    return {"status": "downloading"}


@app.delete("/api/models/{choice}")
def remove_model(choice: str, _: None = Depends(require_local_token)) -> dict:
    if choice not in MODELS:
        raise HTTPException(404, "Неизвестная модель")
    with _operation_lock:
        with _jobs_lock:
            if any(job["model"] == choice and job["status"] in ("queued", "processing")
                   for job in _jobs.values()):
                raise HTTPException(409, "Модель используется для расшифровки. Дождитесь завершения")
        if model_store.download_in_progress(choice):
            raise HTTPException(409, "Нельзя удалить модель во время скачивания")
        unload_model(choice)
        try:
            model_store.delete_model(choice)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
    return {"status": "not_installed"}


@app.post("/api/jobs", status_code=202)
async def create_job(file: UploadFile = File(...), model: str = Form(...),
                     mode: str = Form("standard"), device: str = Form("auto"),
                     _: None = Depends(require_local_token)) -> dict:
    if model not in MODELS:
        raise HTTPException(422, "Выберите поддерживаемую модель")
    if mode not in ('standard', 'vad'):
        raise HTTPException(422, 'Некорректный режим')
    if device not in ('auto', 'cpu', 'cuda'):
        raise HTTPException(422, 'Некорректное устройство')
    if mode == 'vad' and not vad_store.installed():
        raise HTTPException(409, 'Сначала скачайте модель VAD в настройках')
    if device == 'cuda':
        raise HTTPException(409, 'Эта версия GigaScribe работает только на CPU')
    filename = (file.filename or "audio").strip()
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, "Неподдерживаемый формат аудио")

    if not model_store.installed(model):
        raise HTTPException(409, "Модель не установлена. Сначала скачайте её в приложении")

    folder = tempfile.mkdtemp(prefix="gigascribe_")
    # Never use untrusted filename for the filesystem path.
    upload_path = Path(folder) / f"upload{suffix}"
    total = 0
    try:
        with upload_path.open("wb") as stream:
            while data := await file.read(1024 * 1024):
                total += len(data)
                if total > MAX_FILE_BYTES:
                    raise HTTPException(413, "Максимальный размер файла — 200 МБ")
                stream.write(data)
        if total == 0:
            raise HTTPException(400, "Файл пустой")
        job_id = uuid.uuid4().hex
        with _operation_lock:
            if not model_store.installed(model):
                raise HTTPException(409, "Модель удалена. Скачайте её заново")
            with _jobs_lock:
                # Retain only the last 100 jobs' metadata, without raw uploads.
                if len(_jobs) >= 100:
                    completed = [k for k, v in _jobs.items() if v["status"] in ("completed", "failed")]
                    for old_id in completed[: max(1, len(_jobs) - 99)]:
                        del _jobs[old_id]
                _jobs[job_id] = {
                    "id": job_id, "filename": filename, "model": model,
                    "status": "queued", "mode": mode, "device": device, "phase": "queued", "progress": 0,
                    "detail": "В очереди", "error": None, "result": None,
                }
            _executor.submit(_process, job_id, upload_path, folder, model, mode, device)
        return {"job_id": job_id}
    except Exception:
        shutil.rmtree(folder, ignore_errors=True)
        raise
    finally:
        await file.close()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Задача не найдена")
        return dict(job)


@app.get("/")
def index():
    page = DIST / "index.html"
    if page.exists():
        return FileResponse(page)
    return {"message": "Frontend не собран: cd frontend && npm install && npm run build"}


@app.get("/{path:path}")
def static_files(path: str):
    # Only serve files within the prebuilt distribution directory.
    requested = (DIST / path).resolve()
    if not requested.is_relative_to(DIST.resolve()):
        raise HTTPException(404)
    if requested.is_file():
        return FileResponse(requested)
    page = DIST / "index.html"
    if page.exists():
        return FileResponse(page)
    raise HTTPException(404)
