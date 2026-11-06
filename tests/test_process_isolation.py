"""Real desktop/HTTP subprocess regression tests, no actual models or GUI needed."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

import desktop
from backend.transcription import MODELS


def test_window_process_import_does_not_initialize_inference_modules():
    # Use a fresh interpreter; most other tests have already imported torch.
    code = 'import desktop, sys; assert "backend.main" not in sys.modules; assert "torch" not in sys.modules; print("CLEAN")'
    proc = subprocess.run([sys.executable, '-c', code], capture_output=True,
                          text=True, timeout=10, cwd=Path(__file__).resolve().parents[1])
    assert proc.returncode == 0, proc.stderr
    assert 'CLEAN' in proc.stdout


def test_portable_logs_next_to_exe(monkeypatch, tmp_path):
    executable = tmp_path / 'portable' / 'GigaScribe.exe'
    executable.parent.mkdir()
    executable.touch()
    (executable.parent / '.portable').write_text('portable', encoding='ascii')
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.delenv('GIGASCRIBE_LOG_DIR', raising=False)
    assert desktop._log_directory() == executable.parent / 'logs'
    assert (executable.parent / 'logs').is_dir()


def test_installed_app_logs_in_local_appdata(monkeypatch, tmp_path):
    executable = tmp_path / 'installed' / 'GigaScribe.exe'
    executable.parent.mkdir()
    executable.touch()
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', str(executable))
    monkeypatch.delenv('GIGASCRIBE_LOG_DIR', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'AppData' / 'Local'))
    assert desktop._log_directory() == tmp_path / 'AppData' / 'Local' / 'GigaScribe' / 'logs'


def test_actual_backend_subprocess_reports_existing_model_and_denies_mutations(tmp_path):
    from backend.transcription import MODELS
    # Place a complete marker (no real 428-MB checkpoint) into a test-only root.
    models = tmp_path / 'models'
    checkpoint_dir = models / MODELS['russian']
    checkpoint_dir.mkdir(parents=True)
    checkpoint = checkpoint_dir / (MODELS['russian'] + '.ckpt')
    tokenizer = checkpoint_dir / (MODELS['russian'] + '_tokenizer.model')
    checkpoint.write_bytes(b'no-real-weights' * 100)
    tokenizer.write_bytes(b'no-real-tokenizer' * 100)
    (checkpoint_dir / '.ready.json').write_text(json.dumps({
        checkpoint.name: checkpoint.stat().st_size,
        tokenizer.name: tokenizer.stat().st_size,
    }), encoding='utf-8')

    port = desktop._port()
    token = 'sample-token-test-only'
    proc = desktop._launch_backend(port, token, tmp_path, models_dir=models)
    url = f'http://127.0.0.1:{port}'
    try:
        health = desktop._wait_for_backend(proc, port, seconds=15)
        assert health['status'] == 'ok'
        assert health['log_dir'] == str(tmp_path)
        deadline = time.monotonic() + 10
        while True:
            inventory = desktop._request_json(url + '/api/models', timeout=2)
            if not inventory['checking']:
                break
            assert time.monotonic() < deadline, inventory
            time.sleep(.05)
        assert set(inventory['models']) == set(MODELS)
        assert inventory['models']['russian']['installed'] is True
        assert inventory['models']['russian']['path'] == str(checkpoint_dir)
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(url + '/api/models/russian', method='DELETE'), timeout=3)
        assert exc.value.code == 403
        assert proc.poll() is None
        assert (tmp_path / 'backend.log').exists()
    finally:
        desktop._stop_backend(proc)
    assert proc.poll() is not None


def test_packaged_exe_runs_smoke_before_assembler_stage():
    source = Path(__file__).resolve().parents[1] / 'windows' / 'build-windows.ps1'
    script = source.read_text(encoding='ascii')
    assert '& $exe --backend-smoke' in script
    assert script.index('& $exe --backend-smoke') < script.index('$portable =')
    assert 'frameless=not native_frame' in (source.parent.parent / 'desktop.py').read_text(encoding='utf-8')


def test_vad_inventory_uses_ready_manifest_without_recursive_disk_scan(monkeypatch, tmp_path):
    from backend import vad_store
    root = tmp_path / 'models'
    weights = root / 'vad' / 'weights'
    weights.mkdir(parents=True)
    (weights / 'model.safetensors').write_bytes(b'fake-weights')
    (root / 'vad' / '.ready.json').write_text(json.dumps({
        'model.safetensors': len(b'fake-weights'),
    }), encoding='utf-8')
    monkeypatch.setenv('GIGASCRIBE_MODELS_DIR', str(root))
    def forbidden_rglob(self, pattern):
        raise AssertionError('VAD status must not recursively walk model cache')
    monkeypatch.setattr(Path, 'rglob', forbidden_rglob)
    status = vad_store.status()
    assert status['installed'] is True
    assert status['size_bytes'] == len(b'fake-weights')
