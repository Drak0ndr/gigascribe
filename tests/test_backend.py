from pathlib import Path
from tempfile import TemporaryDirectory
import wave
import numpy as np
from fastapi.testclient import TestClient
from backend.main import app
from backend.transcription import split_wav, SAMPLE_RATE, MAX_SEGMENT_SECONDS

client = TestClient(app)


def test_health():
    response = client.get('/api/health')
    assert response.status_code == 200
    assert response.json()['models']['russian'] == 'v3_e2e_rnnt'
    assert response.json()['models']['multilingual'] == 'multilingual_ctc'


def test_reject_model():
    response = client.post('/api/jobs', files={'file': ('test.wav', b'data', 'audio/wav')}, data={'model': 'wrong'})
    assert response.status_code == 422


def test_reject_extension():
    response = client.post('/api/jobs', files={'file': ('bad.exe', b'data', 'application/octet-stream')}, data={'model': 'russian'})
    assert response.status_code == 415


def test_missing_job():
    assert client.get('/api/jobs/does-not-exist').status_code == 404


def test_segmentation_covers_audio_no_overlap():
    with TemporaryDirectory() as d:
        root = Path(d)
        wav_path = root / 'audio.wav'
        duration = 52
        tone = (6000 * np.sin(2 * np.pi * 220 * np.arange(SAMPLE_RATE) / SAMPLE_RATE)).astype('<i2')
        # A clear pause shortly before the 23 second boundary.
        raw = np.tile(tone, duration)
        raw[int(21 * SAMPLE_RATE):int(21.7 * SAMPLE_RATE)] = 0
        with wave.open(str(wav_path), 'wb') as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(SAMPLE_RATE)
            f.writeframes(raw.tobytes())
        segments = split_wav(wav_path, root)
        assert len(segments) >= 3
        assert segments[0][1] <= MAX_SEGMENT_SECONDS
        assert abs(segments[-1][1] - duration) < 0.001
        assert all(end - start <= 25 for start, end, _ in segments)
        for (_, end, _), (next_start, _, _) in zip(segments, segments[1:]):
            assert end == next_start
        assert sum(wave.open(str(path), "rb").getnframes() for _, _, path in segments) == duration * SAMPLE_RATE


def test_local_job_end_to_end_with_fake_model(monkeypatch):
    """Full API + FFmpeg + queue path without downloading proprietary-size weights."""
    import time
    from backend import transcription

    class FakeModel:
        def transcribe(self, path):
            assert Path(path).exists()
            return "Привет, мир!"

    monkeypatch.setattr(transcription, 'load_model', lambda model: FakeModel())
    from backend import model_store
    monkeypatch.setattr(model_store, 'installed', lambda choice: True)
    with TemporaryDirectory() as d:
        wav_path = Path(d) / 'sample.wav'
        with wave.open(str(wav_path), 'wb') as f:
            f.setnchannels(1)
            f.setsampwidth(2)
            f.setframerate(SAMPLE_RATE)
            f.writeframes(np.zeros(SAMPLE_RATE, dtype='<i2').tobytes())
        res = client.post('/api/jobs', files={'file': ('sample.wav', wav_path.read_bytes(), 'audio/wav')}, data={'model':'russian'})
        assert res.status_code == 202, res.text
        job_id = res.json()['job_id']
        for _ in range(80):
            job = client.get(f'/api/jobs/{job_id}').json()
            if job['status'] in ('completed','failed'):
                break
            time.sleep(0.05)
        assert job['status'] == 'completed', job
        assert job['result']['text'] == 'Привет, мир!'
        assert job['result']['segments'][0]['start'] == 0


def test_static_frontend():
    page = client.get('/')
    assert page.status_code == 200
    assert 'GigaScribe' in page.text
    assert client.get('/app.js').status_code == 200
    assert client.get('/styles.css').status_code == 200


def test_models_not_automatically_downloaded(monkeypatch, tmp_path):
    from backend import model_store
    monkeypatch.setenv('GIGASCRIBE_MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.setattr(model_store, '_downloads', {})
    response = client.get('/api/models')
    assert response.status_code == 200
    assert response.json()['models']['russian']['status'] == 'not_installed'
    assert response.json()['models']['multilingual']['status'] == 'not_installed'
    assert not (tmp_path / 'models').exists()


def test_job_requires_download_first(monkeypatch, tmp_path):
    monkeypatch.setenv('GIGASCRIBE_MODELS_DIR', str(tmp_path / 'models'))
    response = client.post('/api/jobs', files={'file': ('audio.wav', b'data', 'audio/wav')}, data={'model': 'russian'})
    assert response.status_code == 409
    assert 'не установлена' in response.json()['detail']


def test_model_download_and_delete_isolated(monkeypatch, tmp_path):
    """Offline simulation of two independently installable models with verified checkpoints."""
    from backend import model_store
    monkeypatch.setenv('GIGASCRIBE_MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.setattr(model_store, '_downloads', {})
    # Avoid network; simulated files still pass the same verified download flow.
    def fake_fetch(choice, destination, progress):
        content = ('checkpoint-' + choice + destination.suffix).encode()
        destination.write_bytes(content)
        progress(len(content), len(content))
    monkeypatch.setattr(model_store, '_fetch_file', fake_fetch)
    monkeypatch.setattr(model_store, '_file_md5', lambda p: model_store.CHECKPOINT_MD5['russian'])
    def create(choice):
        assert model_store.begin_download(choice)
        model_store.download_model(choice)
        assert model_store.installed(choice)
        assert model_store.list_models()['models'][choice]['status'] == 'installed'
    create('russian')
    assert not model_store.installed('multilingual')
    create('multilingual')
    model_store.delete_model('russian')
    assert not model_store.installed('russian')
    assert model_store.installed('multilingual')
    assert not model_store.folder('russian').exists()


def test_download_progress_and_failed_download(monkeypatch, tmp_path):
    from backend import model_store
    monkeypatch.setenv('GIGASCRIBE_MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.setattr(model_store, '_downloads', {})
    def fail_fetch(choice, destination, progress):
        progress(50, 100)
        raise OSError('network temporarily unavailable')
    monkeypatch.setattr(model_store, '_fetch_file', fail_fetch)
    assert model_store.begin_download('russian')
    assert not model_store.begin_download('russian')
    assert model_store.download_in_progress('russian')
    assert model_store.list_models()['models']['russian']['status'] == 'downloading'
    model_store.download_model('russian')
    assert model_store.list_models()['models']['russian']['status'] == 'failed'
    assert not model_store.installed('russian')
    assert model_store.begin_download('russian')  # retry is available


def test_desktop_mutating_api_requires_token(monkeypatch, tmp_path):
    from backend.main import app
    from backend import model_store
    monkeypatch.setenv('GIGASCRIBE_MODELS_DIR', str(tmp_path / 'models'))
    app.state.desktop_token = 'test-secret'
    try:
        assert client.delete('/api/models/russian').status_code == 403
        assert client.post('/api/models/russian/download').status_code == 403
        assert client.post('/api/jobs', files={'file': ('a.wav', b'bits', 'audio/wav')}, data={'model': 'russian'}).status_code == 403
        assert client.get('/api/models').status_code == 200
        assert client.delete('/api/models/russian', headers={'X-GigaScribe-Token':'test-secret'}).status_code == 200
    finally:
        app.state.desktop_token = None


def test_desktop_launcher_local_window(monkeypatch):
    """A fake native window proves the packaged entry point starts/shuts down its local server."""
    import sys
    import types
    from urllib.request import urlopen
    from backend.main import app
    from desktop import main

    observed = {}
    def create_window(title, url, **options):
        observed.update(title=title, url=url, options=options)
        assert 'token=' in url
        with urlopen(url.replace('/?token=', '/api/health?token='), timeout=2) as response:
            assert response.status == 200

    fake_webview = types.SimpleNamespace(create_window=create_window, start=lambda **kwargs: None)
    monkeypatch.setitem(sys.modules, 'webview', fake_webview)
    main()
    assert observed['title'].startswith('GigaScribe')
    assert '127.0.0.1' in observed['url']
    assert app.state.desktop_token
    app.state.desktop_token = None
