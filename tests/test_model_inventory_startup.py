"""Real regression tests for 'Checking models' startup lockup.

These intentionally simulate slow / failed filesystem work, NOT mocked HTTP
responses. The API must answer promptly while the model scan is stuck.
"""
from __future__ import annotations

import threading
import time
from fastapi.testclient import TestClient

from backend import model_inventory, model_store, vad_store
from backend.main import app
from backend.transcription import MODELS


def wait_until(predicate, seconds: float = 2.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(.01)
    return False


def test_models_endpoint_never_blocks_on_hung_filesystem(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    original = model_store.list_models
    def hung_scan():
        started.set()
        if not release.wait(5):
            raise TimeoutError('simulated unavailable disk')
        return original()
    monkeypatch.setattr(model_store, 'list_models', hung_scan)
    model_inventory.reset_for_tests()
    try:
        with TestClient(app) as client:
            begin = time.monotonic()
            initial = client.get('/api/models')
            elapsed = time.monotonic() - begin
            assert initial.status_code == 200
            assert elapsed < 1.5, f'model API waited for a disk scan: {elapsed:.2f}s'
            assert initial.json()['checking']
            assert set(initial.json()['models']) == set(MODELS)
            assert wait_until(started.is_set)
            begin = time.monotonic()
            assert client.get('/api/health').status_code == 200
            assert time.monotonic() - begin < 1.5
            begin = time.monotonic()
            assert client.get('/api/models').status_code == 200
            assert time.monotonic() - begin < 1.5
    finally:
        release.set()
        assert wait_until(lambda: not model_inventory.get_status()['checking'])
        model_inventory.reset_for_tests()


def test_inventory_failure_is_visible_and_recoverable(monkeypatch):
    model_inventory.reset_for_tests()
    monkeypatch.setattr(model_inventory, '_REFRESH_SECONDS', .05)
    def failed():
        raise PermissionError('test disk is locked')
    monkeypatch.setattr(model_store, 'list_models', failed)
    with TestClient(app) as client:
        assert client.get('/api/models').status_code == 200
        assert wait_until(lambda: 'PermissionError' in (client.get('/api/models').json()['scan_error'] or ''))
        monkeypatch.setattr(model_store, 'list_models', lambda: {'path':'C:/models', 'models':{
            k: {'name':n, 'status':'installed', 'installed':True, 'size_bytes':2000,
                'path':'C:/models', 'progress':100,'detail':'','error':None}
            for k,n in MODELS.items()}})
        monkeypatch.setattr(vad_store, 'status', lambda: {'name':'vad','status':'not_installed','installed':False,
            'size_bytes':0,'progress':0,'detail':'','error':None})
        assert wait_until(lambda: client.get('/api/models').json()['models']['russian']['installed'])
        result=client.get('/api/models').json()
        assert result['scan_error'] is None
        assert not result['checking']
    model_inventory.reset_for_tests()


def test_slow_inventory_produces_message_not_endless_loading(monkeypatch):
    start=threading.Event()
    release=threading.Event()
    monkeypatch.setattr(model_inventory, '_SLOW_SECONDS', .02)
    def slow():
        start.set()
        release.wait(2)
        return {'path':'C:/models', 'models': {}}
    monkeypatch.setattr(model_store, 'list_models', slow)
    model_inventory.reset_for_tests()
    try:
        with TestClient(app) as client:
            client.get('/api/models')
            assert wait_until(start.is_set)
            time.sleep(.025)
            result=client.get('/api/models').json()
            assert result['checking']
            assert '8 секунд' in result['scan_error']  # human-facing default message
    finally:
        release.set()
        assert wait_until(lambda: not model_inventory.get_status()['checking'])
        model_inventory.reset_for_tests()


def test_frontend_model_poll_cannot_overlap_and_has_timeout():
    from pathlib import Path
    source=(Path(__file__).resolve().parents[1] / 'frontend' / 'src' / 'App.tsx').read_text(encoding='utf-8')
    assert 'if(inFlight)return' in source
    assert 'AbortController()' in source
    assert "setInterval(()=>void load(),2500)" in source
    assert 'Promise.all([fetch' not in source
    assert "models?.scan_error" in source


def test_vad_scan_cannot_hide_already_detected_sber_models(monkeypatch):
    vad_started = threading.Event()
    vad_release = threading.Event()
    detected = {'path':'C:/models', 'models':{
        k: {'name':n, 'status':'installed', 'installed':True, 'size_bytes':10240,
            'path':'C:/models', 'progress':100, 'detail':'', 'error':None}
        for k,n in MODELS.items()}}
    monkeypatch.setattr(model_store, 'list_models', lambda: detected)
    def slow_vad():
        vad_started.set()
        vad_release.wait(3)
        return {'name':'vad','status':'not_installed','installed':False, 'size_bytes':0,
                'progress':0,'detail':'','error':None}
    monkeypatch.setattr(vad_store, 'status', slow_vad)
    model_inventory.reset_for_tests()
    try:
        with TestClient(app) as client:
            client.get('/api/models')
            assert wait_until(vad_started.is_set)
            result=client.get('/api/models').json()
            assert result['models']['russian']['installed'] is True
            assert result['models']['multilingual_large']['installed'] is True
            assert result['vad']['status'] == 'checking'
            assert result['checking'] is False
    finally:
        vad_release.set()
        assert wait_until(lambda: model_inventory.get_status()['vad']['status'] != 'checking')
        model_inventory.reset_for_tests()
