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
    from backend.main import DIST
    if (DIST / 'index.html').exists():
        assert 'GigaScribe' in page.text
    else:
        assert 'npm run build' in page.text


def test_models_not_automatically_downloaded(monkeypatch, tmp_path):
    from backend import model_store
    monkeypatch.setenv('GIGASCRIBE_MODELS_DIR', str(tmp_path / 'models'))
    monkeypatch.setattr(model_store, '_downloads', {})
    from backend import model_inventory
    import time
    model_inventory.reset_for_tests()
    response = client.get('/api/models')
    assert response.status_code == 200
    # Scanning is deliberately asynchronous. Poll the endpoint, not the filesystem.
    for _ in range(100):
        if not response.json()['checking']:
            break
        time.sleep(.02)
        response = client.get('/api/models')
    assert not response.json()['checking']
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
        content = ('checkpoint-' + choice + destination.suffix).encode() * 200
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

    fake_webview = types.SimpleNamespace(settings={}, create_window=create_window, start=lambda **kwargs: None)
    monkeypatch.setitem(sys.modules, 'webview', fake_webview)
    main()
    assert observed['title'].startswith('GigaScribe')
    assert '127.0.0.1' in observed['url']
    assert app.state.desktop_token is None  # The auth token lives in the isolated child.
    assert observed['options']['frameless'] is False  # Native Win32 frame during recovery.
    assert 'chrome=native' in observed['url']


def test_normalized_pcm_loader_avoids_external_ffmpeg(tmp_path):
    """Regression for WinError 2 from GigaAM's second, unbundled ffmpeg call."""
    import torch
    from backend.transcription import _use_normalized_wav_loader

    wav_path = tmp_path / 'segment.wav'
    samples = np.array([-32768, -16384, 0, 16384, 32767], dtype='<i2')
    with wave.open(str(wav_path), 'wb') as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(samples.tobytes())

    class FakeGigaAM:
        _device = torch.device('cpu')
        _dtype = torch.float32
        def prepare_wav(self, path):
            raise FileNotFoundError(2, 'No such file or directory', 'ffmpeg')
        def transcribe(self, path):
            audio, lengths = self.prepare_wav(path)
            assert lengths.item() == 5
            assert audio.shape == (1, 5)
            assert audio.dtype == torch.float32
            assert audio.device.type == 'cpu'
            assert torch.allclose(audio.squeeze(), torch.from_numpy(samples.astype(np.float32)) / 32768)
            return 'Распознано'

    model = FakeGigaAM()
    _use_normalized_wav_loader(model)
    assert model.transcribe(str(wav_path)) == 'Распознано'


def test_normalized_pcm_loader_rejects_wrong_format(tmp_path):
    import torch
    from backend.transcription import _use_normalized_wav_loader

    wav_path = tmp_path / 'bad.wav'
    with wave.open(str(wav_path), 'wb') as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(SAMPLE_RATE)
        out.writeframes(b'\x00' * 100)

    class FakeGigaAM:
        _device = torch.device('cpu')
        _dtype = torch.float32
    model = FakeGigaAM()
    _use_normalized_wav_loader(model)
    import pytest
    with pytest.raises(ValueError, match='Неверный формат'):
        model.prepare_wav(str(wav_path))


def test_load_model_installs_pcm_adapter(monkeypatch, tmp_path):
    """The actual model path must wire up the non-FFmpeg decoder."""
    import sys
    import types
    import torch
    from backend import transcription, model_store

    class FakeModel:
        _device = torch.device('cpu')
        _dtype = torch.float32
        def prepare_wav(self, path):
            raise FileNotFoundError(2, 'No such file or directory', 'ffmpeg')

    fake = FakeModel()
    monkeypatch.setitem(sys.modules, 'gigaam', types.SimpleNamespace(load_model=lambda *a, **kw: fake))
    monkeypatch.setattr(model_store, 'installed', lambda choice: True)
    monkeypatch.setattr(transcription, '_current_model', None)
    monkeypatch.setattr(transcription, '_current_model_name', None)
    wav_path = tmp_path / 'test.wav'
    with wave.open(str(wav_path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(b'\x00\x00' * SAMPLE_RATE)
    model = transcription.load_model('russian')
    assert model is fake
    audio, lengths = model.prepare_wav(str(wav_path))
    assert audio.shape == (1, SAMPLE_RATE)
    assert lengths.item() == SAMPLE_RATE


def test_desktop_self_check_without_models(monkeypatch, tmp_path):
    """The frozen EXE smoke check must not download any model weights."""
    import sys
    import types
    import tempfile
    import torch
    import backend.main as backend_main
    from desktop import self_check

    frontend = tmp_path / 'frontend-dist'
    (frontend / 'assets').mkdir(parents=True)
    (frontend / 'index.html').write_text('<div>GigaScribe</div>', encoding='utf-8')
    monkeypatch.setattr(backend_main, 'DIST', frontend)
    monkeypatch.setattr(torch.version, 'cuda', None)
    monkeypatch.setitem(sys.modules, 'gigaam', types.SimpleNamespace(load_model=lambda: None))
    # Simulate the dynamically imported pyannote checkpoint architecture.
    # This is the exact import that was absent from the v1.3.2 frozen EXE.
    pyannote = types.ModuleType('pyannote')
    pyannote.__path__ = []
    audio = types.ModuleType('pyannote.audio')
    audio.__path__ = []
    models = types.ModuleType('pyannote.audio.models')
    models.__path__ = []
    segmentation = types.ModuleType('pyannote.audio.models.segmentation')
    segmentation.__path__ = []
    blocks = types.ModuleType('pyannote.audio.models.blocks')
    blocks.__path__ = []
    pynet_module = types.ModuleType('pyannote.audio.models.segmentation.PyanNet')
    sincnet_module = types.ModuleType('pyannote.audio.models.blocks.sincnet')
    class FakePyannoteModel: pass
    class FakePyanNet(FakePyannoteModel): pass
    class FakeSincNet: pass
    audio.Model = FakePyannoteModel
    pynet_module.PyanNet = FakePyanNet
    sincnet_module.SincNet = FakeSincNet
    pyannote.audio = audio
    for name, mod in {
        'pyannote': pyannote,
        'pyannote.audio': audio,
        'pyannote.audio.models': models,
        'pyannote.audio.models.segmentation': segmentation,
        'pyannote.audio.models.blocks': blocks,
        'pyannote.audio.models.segmentation.PyanNet': pynet_module,
        'pyannote.audio.models.blocks.sincnet': sincnet_module,
        'torchcodec': types.ModuleType('torchcodec'),
    }.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(tempfile, 'tempdir', str(tmp_path))
    assert self_check() == 0, (tmp_path / 'gigascribe-desktop-self-check.log').read_text(encoding='utf-8')
    assert 'OK:' in (tmp_path / 'gigascribe-desktop-self-check.log').read_text(encoding='utf-8')


def test_cpu_only_and_cuda_is_rejected():
    from backend import transcription
    assert transcription.resolve_device('cpu') == 'cpu'
    assert transcription.resolve_device('auto') == 'cpu'
    import pytest
    with pytest.raises(RuntimeError, match='только на CPU'):
        transcription.resolve_device('cuda')
    assert transcription.device_info()['cuda_available'] is False


def test_vad_requires_independent_download(monkeypatch, tmp_path):
    from backend import vad_store
    monkeypatch.setenv('GIGASCRIBE_MODELS_DIR', str(tmp_path / 'models'))
    assert not vad_store.installed()
    response=client.post('/api/jobs',files={'file':('x.wav',b'audio')},data={'model':'russian','mode':'vad'})
    assert response.status_code==409
    assert 'VAD' in response.json()['detail']
    assert client.post('/api/vad/download',json={'token':''}).status_code==422


def test_vad_store_download_delete_no_network(monkeypatch,tmp_path):
    from backend import vad_store
    from pathlib import Path
    import sys
    import types
    monkeypatch.setenv('GIGASCRIBE_MODELS_DIR', str(tmp_path / 'models'))
    (vad_store.weights_dir() / 'config.yaml').parent.mkdir(parents=True)
    def snapshot_download(*, repo_id, local_dir, token, ignore_patterns):
        assert token=='hf_test'
        assert repo_id=='pyannote/segmentation-3.0'
        Path(local_dir,'model.safetensors').write_bytes(b'weights')
        Path(local_dir,'config.yaml').write_bytes(b'config')
    monkeypatch.setitem(sys.modules, 'huggingface_hub', types.SimpleNamespace(snapshot_download=snapshot_download))
    monkeypatch.setattr(vad_store,'_state',{'status':'not_installed','progress':0,'detail':'','error':None})
    assert vad_store.begin_download()
    assert not vad_store.begin_download()
    vad_store.download('hf_test')
    assert vad_store.installed()
    assert vad_store.status()['installed'] is True
    vad_store.delete()
    assert not vad_store.installed()


def test_vad_patch_disables_external_file_decoder(monkeypatch,tmp_path):
    """Ensure official longform gets a decoded tensor rather than requiring FFmpeg."""
    import sys,types
    from backend import transcription, vad_store
    import torch
    monkeypatch.setattr(vad_store, 'installed', lambda:True)
    monkeypatch.setattr(vad_store, 'weights_dir', lambda:tmp_path)
    mod=types.ModuleType('gigaam.vad_utils')
    class Pipeline:
        def __call__(self, data):
            assert data['sample_rate']==16000
            assert tuple(data['waveform'].shape)==(1,16000)
            return 'segments'
    mod.get_pipeline=lambda device, model_id='pyannote/segmentation-3.0':Pipeline()
    mod._PIPELINE=None
    monkeypatch.setitem(sys.modules, 'gigaam', types.ModuleType('gigaam'))
    monkeypatch.setitem(sys.modules,'gigaam.vad_utils',mod)
    with wave.open(str(tmp_path/'x.wav'),'wb') as f:
        f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000)
        f.writeframes(b'\x00\x00'*16000)
    transcription._prepare_vad_adapter()
    assert mod.get_pipeline(torch.device('cpu'))(str(tmp_path/'x.wav'))=='segments'


def test_longform_uses_official_api_with_vad(monkeypatch, tmp_path):
    """Regression: official GigaAM longform, not our custom split_wav path."""
    import types
    from backend import transcription
    import wave
    sample = tmp_path / 'input.wav'
    with wave.open(str(sample), 'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
        w.writeframes(b'\x00\x00' * 32000)

    class FakeModel:
        def transcribe_longform(self, path, fr_batch_size, fr_num_workers):
            assert Path(path).name == 'converted.wav'
            assert fr_batch_size == 2
            assert fr_num_workers == 0
            return types.SimpleNamespace(segments=[types.SimpleNamespace(start=0.2, end=1.7, text='Правильная речь')])

    monkeypatch.setattr(transcription, 'convert_to_wav', lambda src, dst: dst.write_bytes(sample.read_bytes()))
    monkeypatch.setattr(transcription, 'load_model', lambda *args, **kw: FakeModel())
    monkeypatch.setattr(transcription, '_prepare_vad_adapter', lambda: None)
    monkeypatch.setattr(transcription, 'resolve_device', lambda d: 'cpu')
    monkeypatch.setattr(transcription, 'split_wav', lambda *args: (_ for _ in ()).throw(AssertionError('Custom splitter used')))
    result = transcription.transcribe_audio(sample, tmp_path, 'russian', lambda *args:None, 'vad', 'cpu')
    assert result['text'] == 'Правильная речь'
    assert result['segments'][0]['start'] == 0.2
    assert result['mode'] == 'vad'


def test_native_text_save_dialog(tmp_path, monkeypatch):
    import sys,types
    from desktop import WindowAPI
    target = tmp_path / 'transcript.txt'
    fake_view = types.SimpleNamespace(SAVE_DIALOG='SAVE')
    monkeypatch.setitem(sys.modules, 'webview', fake_view)

    class FakeWindow:
        def create_file_dialog(self, *args, **kwargs):
            assert kwargs['save_filename'] == 'record.txt'
            return (str(target),)
    api = WindowAPI()
    api.window = FakeWindow()
    assert api.save_text('record.txt', 'Привет, React!') is True
    assert target.read_text(encoding='utf-8-sig') == 'Привет, React!'
    assert target.read_bytes().startswith(b'\xef\xbb\xbf')
