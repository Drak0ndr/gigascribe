"""Regressions for the v1.3.4 portable storage and CPU-only rollback.

No model download and no GPU required.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import model_store
from backend.main import app
from backend.transcription import MODELS, resolve_device


def fake_model_files(root: Path, choice: str, *, marker: bool = True):
    name = MODELS[choice]
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    names = [name + '.ckpt'] + ([name + '_tokenizer.model'] if 'e2e' in name else [])
    files = []
    for filename in names:
        path = directory / filename
        path.write_bytes(b'FAKE WEIGHTS' * 140)
        files.append(path)
    if marker:
        (directory / '.ready.json').write_text(
            json.dumps({p.name: p.stat().st_size for p in files}), encoding='utf-8')
    return directory


def simulate_frozen_windows(monkeypatch, tmp_path: Path, portable: bool = True):
    exe = tmp_path / 'portable' / 'GigaScribe.exe'
    exe.parent.mkdir(exist_ok=True)
    exe.write_bytes(b'exe')
    if portable:
        (exe.parent / '.portable').write_text('portable', encoding='ascii')
    local = tmp_path / 'AppData' / 'Local'
    monkeypatch.delenv('GIGASCRIBE_MODELS_DIR', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(local))
    monkeypatch.setattr(model_store.sys, 'platform', 'win32')
    monkeypatch.setattr(model_store.sys, 'frozen', True, raising=False)
    monkeypatch.setattr(model_store.sys, 'executable', str(exe))
    return exe.parent / 'models', local / 'GigaScribe' / 'models'


def test_portable_finds_previously_installed_models_and_uses_existing_paths(monkeypatch, tmp_path):
    portable, installed = simulate_frozen_windows(monkeypatch, tmp_path)
    old = fake_model_files(installed, 'russian')
    assert model_store.storage_root() == portable
    assert model_store.installed('russian')
    assert model_store.folder('russian') == old
    info = model_store.list_models()['models']['russian']
    assert info['installed'] and info['path'] == str(old)
    assert info['size_bytes'] > 2000
    assert not portable.exists()  # Do not duplicate already-downloaded weights.


def test_new_model_downloads_use_portable_root_and_detect_all_four(monkeypatch, tmp_path):
    portable, installed = simulate_frozen_windows(monkeypatch, tmp_path)
    assert len(MODELS) == 4
    for choice in MODELS:
        directory = fake_model_files(portable, choice)
        assert model_store.installed(choice)
        assert model_store.folder(choice) == directory
    assert model_store.list_models()['models'].keys() == MODELS.keys()
    assert not installed.exists()


def test_installed_build_can_detect_portable_weights_without_marker_file(monkeypatch, tmp_path):
    portable, installed = simulate_frozen_windows(monkeypatch, tmp_path, portable=False)
    # No .portable marker => normal installed app; also accepts a previous
    # portable folder if its known files are present.
    legacy = fake_model_files(portable, 'multilingual_large', marker=False)
    assert model_store.storage_root() == installed
    assert model_store.installed('multilingual_large')
    assert model_store.folder('multilingual_large') == legacy


def test_incomplete_legacy_model_not_marked_as_installed(monkeypatch, tmp_path):
    monkeypatch.setenv('GIGASCRIBE_MODELS_DIR', str(tmp_path / 'models'))
    directory = fake_model_files(tmp_path / 'models', 'russian_ctc', marker=False)
    (directory / 'v3_e2e_ctc.ckpt.part').write_bytes(b'partial')
    assert not model_store.installed('russian_ctc')
    (directory / 'v3_e2e_ctc.ckpt.part').unlink()
    assert model_store.installed('russian_ctc')  # Checkpoint MD5 checked by GigaAM on use.
    (directory / 'v3_e2e_ctc_tokenizer.model').unlink()
    assert not model_store.installed('russian_ctc')


def test_ready_marker_does_not_mask_bad_checkpoint_size(monkeypatch, tmp_path):
    monkeypatch.setenv('GIGASCRIBE_MODELS_DIR', str(tmp_path / 'models'))
    directory = fake_model_files(tmp_path / 'models', 'multilingual')
    (directory / 'multilingual_ctc.ckpt').write_bytes(b'broken')
    assert not model_store.installed('multilingual')


def test_cpu_only_rejects_cuda_without_launching_job(monkeypatch, tmp_path):
    monkeypatch.setenv('GIGASCRIBE_MODELS_DIR', str(tmp_path / 'models'))
    fake_model_files(tmp_path / 'models', 'russian')
    client = TestClient(app)
    r = client.post('/api/jobs', files={'file': ('test.wav', b'bytes', 'audio/wav')},
                    data={'model': 'russian', 'device': 'cuda'})
    assert r.status_code == 409
    assert resolve_device('auto') == resolve_device('cpu') == 'cpu'
    with pytest.raises(RuntimeError, match='CPU'):
        resolve_device('cuda')


def test_official_model_ids_and_hashes():
    assert MODELS == {
        'russian': 'v3_e2e_rnnt',
        'russian_ctc': 'v3_e2e_ctc',
        'multilingual': 'multilingual_ctc',
        'multilingual_large': 'multilingual_large_ctc',
    }
    assert model_store.CHECKPOINT_MD5 == {
        'russian': '2730de7545ac43ad256485a462b0a27a',
        'russian_ctc': '367074d6498f426d960b25f49531cf68',
        'multilingual': '5379d887c53ccd9cb95981e2a1832720',
        'multilingual_large': '79a9adde50dd7f35bbf70927cb6557d0',
    }


def test_deleting_legacy_model_does_not_touch_other_models(monkeypatch, tmp_path):
    portable, installed = simulate_frozen_windows(monkeypatch, tmp_path)
    legacy = fake_model_files(installed, 'russian')
    another = fake_model_files(installed, 'multilingual')
    model_store.delete_model('russian')
    assert not legacy.exists()
    assert model_store.installed('multilingual')
    assert another.exists()


def test_vad_checkpoint_found_in_old_installed_location(monkeypatch, tmp_path):
    from backend import vad_store
    portable, installed = simulate_frozen_windows(monkeypatch, tmp_path)
    root = installed / 'vad'
    weight = root / 'weights' / 'model.safetensors'
    weight.parent.mkdir(parents=True)
    weight.write_bytes(b'old-vad-weights')
    (root / '.ready.json').write_text(
        json.dumps({'model.safetensors': len(b'old-vad-weights')}), encoding='utf-8')
    assert vad_store.installed()
    assert vad_store.base_dir() == root
    assert not portable.exists()
