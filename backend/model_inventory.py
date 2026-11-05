"""Non-blocking and independent ASR/VAD status snapshots for the desktop UI.

Disk enumeration MUST NOT happen in an HTTP route. In particular, a slow
Hugging Face cache must not hide already installed Sber ASR models, prevent a
later ASR refresh, or block the local server during window startup.
"""
from __future__ import annotations

import copy
import logging
import threading
import time

from backend import model_store, vad_store
from backend.transcription import MODELS

_LOG = logging.getLogger(__name__)
_LOCK = threading.Lock()
_MODEL_SNAPSHOT: dict | None = None
_VAD_SNAPSHOT: dict | None = None
_MODEL_CHECKING = False
_VAD_CHECKING = False
_MODEL_STARTED = 0.0
_VAD_STARTED = 0.0
_MODEL_COMPLETED = 0.0
_VAD_COMPLETED = 0.0
_MODEL_ERROR: str | None = None
_VAD_ERROR: str | None = None
_REFRESH_SECONDS = 5.0
_SLOW_SECONDS = 8.0


def _pending_models() -> dict:
    return {
        'path': '',
        'models': {
            key: {'name': name, 'status': 'checking', 'installed': False,
                  'size_bytes': 0, 'path': '', 'progress': 0,
                  'detail': '', 'error': None}
            for key, name in MODELS.items()
        },
    }


def _pending_vad() -> dict:
    return {'name': 'pyannote/segmentation-3.0', 'status': 'checking',
            'installed': False, 'size_bytes': 0, 'progress': 0,
            'detail': '', 'error': None}


def _scan_models() -> None:
    global _MODEL_SNAPSHOT, _MODEL_CHECKING, _MODEL_COMPLETED, _MODEL_ERROR
    started = time.monotonic()
    _LOG.info('Sber model scan started')
    try:
        result = model_store.list_models()
    except Exception as exc:
        _LOG.exception('Sber model scan failed')
        with _LOCK:
            _MODEL_ERROR = f'{type(exc).__name__}: {exc}'
    else:
        with _LOCK:
            _MODEL_SNAPSHOT = result
            _MODEL_ERROR = None
    finally:
        with _LOCK:
            _MODEL_CHECKING = False
            _MODEL_COMPLETED = time.monotonic()
        _LOG.info('Sber model scan finished in %.2fs', time.monotonic() - started)


def _scan_vad() -> None:
    global _VAD_SNAPSHOT, _VAD_CHECKING, _VAD_COMPLETED, _VAD_ERROR
    started = time.monotonic()
    _LOG.info('VAD scan started')
    try:
        result = vad_store.status()
    except Exception as exc:
        _LOG.exception('VAD scan failed')
        with _LOCK:
            _VAD_ERROR = f'{type(exc).__name__}: {exc}'
    else:
        with _LOCK:
            _VAD_SNAPSHOT = result
            _VAD_ERROR = None
    finally:
        with _LOCK:
            _VAD_CHECKING = False
            _VAD_COMPLETED = time.monotonic()
        _LOG.info('VAD scan finished in %.2fs', time.monotonic() - started)


def get_status() -> dict:
    """Constant-time status: all filesystem work is in independent daemon threads."""
    global _MODEL_CHECKING, _VAD_CHECKING, _MODEL_STARTED, _VAD_STARTED
    now = time.monotonic()
    with _LOCK:
        if not _MODEL_CHECKING and (not _MODEL_COMPLETED or now - _MODEL_COMPLETED >= _REFRESH_SECONDS):
            _MODEL_CHECKING = True
            _MODEL_STARTED = now
            threading.Thread(target=_scan_models, name='gigascribe-model-scan', daemon=True).start()
        if not _VAD_CHECKING and (not _VAD_COMPLETED or now - _VAD_COMPLETED >= _REFRESH_SECONDS):
            _VAD_CHECKING = True
            _VAD_STARTED = now
            threading.Thread(target=_scan_vad, name='gigascribe-vad-scan', daemon=True).start()

        result = copy.deepcopy(_MODEL_SNAPSHOT) if _MODEL_SNAPSHOT is not None else _pending_models()
        result['vad'] = copy.deepcopy(_VAD_SNAPSHOT) if _VAD_SNAPSHOT is not None else _pending_vad()
        result['checking'] = _MODEL_CHECKING and _MODEL_SNAPSHOT is None

        problems = []
        if _MODEL_ERROR:
            problems.append(f'Ошибка проверки моделей: {_MODEL_ERROR}')
        if _VAD_ERROR:
            problems.append(f'Ошибка проверки VAD: {_VAD_ERROR}')
        if _MODEL_CHECKING and now - _MODEL_STARTED >= _SLOW_SECONDS:
            problems.append('Поиск моделей занимает больше 8 секунд. Проверьте доступность диска с моделями.')
        if _VAD_CHECKING and now - _VAD_STARTED >= _SLOW_SECONDS:
            problems.append('Проверка VAD занимает больше 8 секунд. Основные модели доступны отдельно.')
        result['scan_error'] = ' '.join(problems) if problems else None
        return result


def reset_for_tests() -> None:
    """Clear the cache after worker threads have completed (tests only)."""
    global _MODEL_SNAPSHOT, _VAD_SNAPSHOT, _MODEL_CHECKING, _VAD_CHECKING
    global _MODEL_STARTED, _VAD_STARTED, _MODEL_COMPLETED, _VAD_COMPLETED
    global _MODEL_ERROR, _VAD_ERROR
    with _LOCK:
        _MODEL_SNAPSHOT = None
        _VAD_SNAPSHOT = None
        _MODEL_CHECKING = False
        _VAD_CHECKING = False
        _MODEL_STARTED = 0.0
        _VAD_STARTED = 0.0
        _MODEL_COMPLETED = 0.0
        _VAD_COMPLETED = 0.0
        _MODEL_ERROR = None
        _VAD_ERROR = None
