"""Explicit, on-demand GigaAM model downloads, storage and safe deletion.

This module never imports PyTorch/GigaAM and never downloads anything on import.
Sources and checkpoint hashes match the upstream GigaAM model loader.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import threading
import urllib.request
from pathlib import Path
from typing import Callable

from backend.transcription import MODELS

CDN = 'https://cdn.chatwm.opensmodel.sberdevices.ru/GigaAM'
# GigaAM _MODEL_HASHES: https://github.com/salute-developers/GigaAM/blob/main/gigaam/__init__.py
CHECKPOINT_MD5 = {
    'russian': '2730de7545ac43ad256485a462b0a27a',
    'multilingual': '5379d887c53ccd9cb95981e2a1832720',
    'russian_ctc': '367074d6498f426d960b25f49531cf68',
    'multilingual_large': '79a9adde50dd7f35bbf70927cb6557d0',
}
STATE_LOCK = threading.RLock()
_downloads: dict[str, dict] = {}


def storage_root() -> Path:
    """Development: ./models. Packaged: user data; optional portable override.

    Program Files and macOS .app are generally read-only. Portable users can
    set GIGASCRIBE_MODELS_DIR or put a `.portable` marker next to the exe.
    """
    override = os.environ.get('GIGASCRIBE_MODELS_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if getattr(sys, 'frozen', False):
        exe_folder = Path(sys.executable).resolve().parent
        if (exe_folder / '.portable').exists():
            return exe_folder / 'models'
        if sys.platform == 'win32':
            base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
            return base / 'GigaScribe' / 'models'
        if sys.platform == 'darwin':
            return Path.home() / 'Library' / 'Application Support' / 'GigaScribe' / 'models'
        return Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'GigaScribe' / 'models'
    return Path(__file__).resolve().parent.parent / 'models'


def storage_roots() -> tuple[Path, ...]:
    """First root is the desired download location; later roots contain old data.

    A portable build used to hide previously installed models by unconditionally
    switching the root. We locate existing checkpoints without copying gigabytes.
    An explicit override intentionally disables fallback (including in tests).
    """
    primary = storage_root()
    if os.environ.get('GIGASCRIBE_MODELS_DIR'):
        return (primary,)
    candidates = [primary]
    if sys.platform == 'win32':
        local = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local') / 'GigaScribe' / 'models'
        candidates.append(local)
        if getattr(sys, 'frozen', False):
            candidates.append(Path(sys.executable).resolve().parent / 'models')
    # Older upstream GigaAM downloads are flat files in its default cache.
    candidates.append(Path.home() / '.cache' / 'gigaam')
    return tuple(dict.fromkeys(candidates))


def _paths_at(root: Path, choice: str) -> tuple[Path, ...]:
    name = MODELS[choice]
    base = root / name
    # GigaScribe stores a separate folder per model. The upstream cache is flat.
    if root.name == 'gigaam' and root.parent.name == '.cache':
        base = root
    names = [name + '.ckpt']
    if 'e2e' in name:
        names.append(name + '_tokenizer.model')
    return tuple(base / filename for filename in names)


def _is_complete(root: Path, choice: str) -> bool:
    """Fast path using our ready marker; accept old complete GigaAM files too.

    Legacy checkpoints without a marker are verified by GigaAM's official MD5
    check when loaded. Never accept temporary *.part downloads as complete.
    """
    files = _paths_at(root, choice)
    if any(p.with_name(p.name + '.part').exists() for p in files):
        return False
    if not all(p.is_file() and p.stat().st_size > 1024 for p in files):
        return False
    ready = files[0].parent / '.ready.json'
    if not ready.exists():
        return True  # Legacy models saved before .ready.json was introduced.
    try:
        expected = json.loads(ready.read_text(encoding='utf-8'))
        return all(p.stat().st_size == expected[p.name] for p in files)
    except (ValueError, OSError, KeyError, TypeError):
        return False


def folder(choice: str) -> Path:
    if choice not in MODELS:
        raise ValueError('Неизвестная модель')
    for root in storage_roots():
        if _is_complete(root, choice):
            return _paths_at(root, choice)[0].parent
    return storage_root() / MODELS[choice]


def files_for(choice: str) -> tuple[Path, ...]:
    name = MODELS[choice]
    base = folder(choice)
    names = [name + '.ckpt']
    if 'e2e' in name:
        names.append(name + '_tokenizer.model')
    return tuple(base / filename for filename in names)


def installed(choice: str) -> bool:
    return any(_is_complete(root, choice) for root in storage_roots())


def _used_bytes(choice: str) -> int:
    # Count only selected model files, not the whole legacy shared GigaAM cache.
    return sum(p.stat().st_size for p in files_for(choice) if p.is_file() and not p.is_symlink())


def list_models() -> dict:
    with STATE_LOCK:
        models = {}
        for choice, name in MODELS.items():
            current = dict(_downloads.get(choice) or {})
            has_files = installed(choice)
            status = ('downloading' if current.get('status') == 'downloading' else
                      'installed' if has_files else
                      'failed' if current.get('status') == 'failed' else 'not_installed')
            models[choice] = {
                'name': name,
                'status': status,
                'installed': has_files,
                'size_bytes': _used_bytes(choice),
                'path': str(folder(choice)),
                'progress': current.get('progress', 0) if status == 'downloading' else 100 if has_files else 0,
                'detail': current.get('detail', '') if status in ('downloading', 'failed') else '',
                'error': current.get('error') if status == 'failed' else None,
            }
        return {'path': str(storage_root()), 'models': models}


def download_in_progress(choice: str) -> bool:
    with STATE_LOCK:
        return _downloads.get(choice, {}).get('status') == 'downloading'


def begin_download(choice: str) -> bool:
    """Atomically reserve the download slot. Returns False if already installed/busy."""
    with STATE_LOCK:
        if download_in_progress(choice) or installed(choice):
            return False
        _downloads[choice] = {'status': 'downloading', 'progress': 0, 'detail': 'Подготовка загрузки', 'error': None}
        return True


def _update(choice: str, **changes) -> None:
    with STATE_LOCK:
        _downloads[choice].update(changes)


def _fetch_file(choice: str, destination: Path, progress: Callable[[int, int], None]) -> None:
    """Stream from pinned Sber CDN, atomically publish only a complete download."""
    url = f'{CDN}/{destination.name}'
    temp_path = destination.with_name(destination.name + '.part')
    expected_md5 = CHECKPOINT_MD5[choice] if destination.suffix == '.ckpt' else None
    hasher = hashlib.md5() if expected_md5 else None
    downloaded = 0
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'GigaScribe/1.1'})
        with urllib.request.urlopen(request, timeout=60) as source, temp_path.open('wb') as out:
            total = int(source.headers.get('Content-Length') or 0)
            while True:
                block = source.read(1024 * 1024)
                if not block:
                    break
                out.write(block)
                downloaded += len(block)
                if hasher:
                    hasher.update(block)
                progress(downloaded, total)
        if not downloaded or (total and downloaded != total):
            raise RuntimeError('Неполная загрузка файла модели')
        if hasher and hasher.hexdigest() != expected_md5:
            raise RuntimeError('Контрольная сумма весов не совпадает с оригинальной GigaAM')
        temp_path.replace(destination)
    finally:
        temp_path.unlink(missing_ok=True)


def download_model(choice: str) -> None:
    """Called after begin_download(), in worker thread; never implicitly triggered."""
    try:
        root = folder(choice)
        root.mkdir(parents=True, exist_ok=True)
        files = files_for(choice)
        for index, destination in enumerate(files):
            if destination.is_file() and destination.stat().st_size > 0:
                # Existing checkpoint is verified below as well, so stale/corrupted
                # files don't make their way into GigaAM.
                if destination.suffix != '.ckpt' or _file_md5(destination) == CHECKPOINT_MD5[choice]:
                    continue
                destination.unlink()
            def progress(done: int, total: int, index=index) -> None:
                fraction = (done / total) if total else 0
                pct = min(99, int(100 * (index + fraction) / len(files)))
                _update(choice, progress=pct,
                        detail=f'Файл {index+1}/{len(files)}: {done / 1024 / 1024:.1f} МБ' +
                               (f' из {total / 1024 / 1024:.1f} МБ' if total else ''))
            _fetch_file(choice, destination, progress)
        if not all(p.is_file() and p.stat().st_size > 0 for p in files):
            raise RuntimeError('Загружены не все файлы модели')
        ready = root / '.ready.json'
        ready_tmp = root / '.ready.json.part'
        ready_tmp.write_text(json.dumps({p.name: p.stat().st_size for p in files}), encoding='utf-8')
        ready_tmp.replace(ready)
        _update(choice, status='installed', progress=100, detail='Модель установлена', error=None)
    except Exception as exc:
        _update(choice, status='failed', progress=0, detail='Ошибка загрузки', error=str(exc))


def _file_md5(path: Path) -> str:
    hasher = hashlib.md5()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(4 * 1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def delete_model(choice: str) -> None:
    """Caller must check that there is no queued/running task using this model."""
    with STATE_LOCK:
        if download_in_progress(choice):
            raise RuntimeError('Нельзя удалить модель во время скачивания')
        base = folder(choice)
        # Don't follow unexpected directory links during deletion.
        if base.is_symlink():
            raise RuntimeError('Нельзя удалить папку модели, являющуюся ссылкой')
        for p in files_for(choice):
            if p.is_file() or p.is_symlink():
                p.unlink()
            part = p.with_name(p.name + '.part')
            if part.is_file() or part.is_symlink():
                part.unlink()
        for marker in (base / '.ready.json', base / '.ready.json.part'):
            if marker.is_file() or marker.is_symlink():
                marker.unlink()
        if base.is_dir() and not any(base.iterdir()):
            base.rmdir()
        _downloads.pop(choice, None)
