"""Optional, gated pyannote VAD checkpoint. Never fetched automatically."""
from __future__ import annotations

import json
import shutil
import threading
from pathlib import Path

from backend.model_store import storage_root, storage_roots

LOCK = threading.RLock()
_state = {'status': 'not_installed', 'progress': 0, 'detail': '', 'error': None}


def base_dir() -> Path:
    for root in storage_roots():
        candidate = root / 'vad'
        marker = candidate / '.ready.json'
        if marker.is_file():
            try:
                files = json.loads(marker.read_text(encoding='utf-8'))
                if files and all((candidate / 'weights' / name).is_file()
                                 and (candidate / 'weights' / name).stat().st_size == size
                                 for name, size in files.items()):
                    return candidate
            except (OSError, ValueError, TypeError):
                pass
    return storage_root() / 'vad'


def weights_dir() -> Path:
    return base_dir() / 'weights'


def installed() -> bool:
    ready = base_dir() / '.ready.json'
    if not ready.is_file():
        return False
    try:
        files = json.loads(ready.read_text(encoding='utf-8'))
        return bool(files) and all((weights_dir() / name).is_file() and (weights_dir() / name).stat().st_size == size
                                   for name, size in files.items())
    except (OSError, ValueError, TypeError):
        return False


def status() -> dict:
    with LOCK:
        state = dict(_state)
    # The old status endpoint recursively traversed the entire Hugging Face
    # snapshot (.cache included) on every inventory refresh. On Windows this
    # can hold the filesystem scan for a long time. We only need the files
    # listed in the ready manifest, which we wrote during the download.
    directory = base_dir()
    ready = directory / '.ready.json'
    has = False
    size = 0
    try:
        if ready.is_file():
            expected = json.loads(ready.read_text(encoding='utf-8'))
            if isinstance(expected, dict) and expected:
                weights = directory / 'weights'
                entries = [(weights / name, expected_size) for name, expected_size in expected.items()]
                has = all(isinstance(expected_size, int) and expected_size > 0 and
                          path.is_file() and path.stat().st_size == expected_size
                          for path, expected_size in entries)
                if has:
                    size = sum(expected.values())
    except (OSError, ValueError, TypeError, KeyError):
        pass
    status_name = state['status'] if state['status'] in ('downloading', 'failed') else ('installed' if has else 'not_installed')
    return {'name': 'pyannote/segmentation-3.0', 'status': status_name, 'installed': has,
            'size_bytes': size, 'progress': state['progress'] if status_name == 'downloading' else (100 if has else 0),
            'detail': state['detail'], 'error': state['error'] if status_name == 'failed' else None}


def begin_download() -> bool:
    with LOCK:
        if installed() or _state['status'] == 'downloading':
            return False
        _state.update(status='downloading', progress=5, detail='Подключение к Hugging Face', error=None)
        return True


def download(token: str) -> None:
    """HF access token is only kept in-memory until the background job finishes."""
    try:
        from huggingface_hub import snapshot_download
        dest = weights_dir()
        dest.mkdir(parents=True, exist_ok=True)
        # local_dir keeps the requested checkpoint in GigaScribe's model directory.
        snapshot_download(repo_id='pyannote/segmentation-3.0', local_dir=str(dest), token=token,
                          ignore_patterns=['*.md', '.gitattributes'])
        files = {str(p.relative_to(dest)).replace('\\', '/'): p.stat().st_size
                 for p in dest.rglob('*') if p.is_file() and p.stat().st_size > 0
                 and '.cache' not in p.parts and not p.is_symlink()}
        # This repo has a model checkpoint and a config; avoid marking an empty dir as ready.
        if not any(x.endswith(('.bin', '.safetensors', '.ckpt')) for x in files):
            raise RuntimeError('Не обнаружены веса VAD — проверьте доступ к репозиторию')
        marker = base_dir() / '.ready.json'
        temp = marker.with_suffix('.part')
        temp.write_text(json.dumps(files), encoding='utf-8')
        temp.replace(marker)
        with LOCK:
            _state.update(status='installed', progress=100, detail='VAD установлена', error=None)
    except Exception as exc:
        with LOCK:
            _state.update(status='failed', progress=0, detail='Ошибка загрузки', error=str(exc))


def delete() -> None:
    with LOCK:
        if _state['status'] == 'downloading':
            raise RuntimeError('VAD загружается')
        root = base_dir()
        if root.is_symlink():
            raise RuntimeError('Запрещено удалять папку-ссылку')
        # Never delete outside of GigaScribe model storage.
        if root.is_dir():
            shutil.rmtree(root)
        _state.update(status='not_installed', progress=0, detail='', error=None)
    # Release pyannote VAD pipeline cache on removal.
    try:
        import gigaam.vad_utils as vad_utils
        vad_utils._PIPELINE = None
    except ImportError:
        pass
