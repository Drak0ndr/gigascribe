"""Desktop launcher: native window isolated from a separate local ASR server process.

The installer bundles Python, the UI, FFmpeg and the inference runtime.
Model checkpoints are NOT included and are only downloaded on user request.
"""
from __future__ import annotations

import secrets
import os
import re
import socket
import subprocess
import sys
import tempfile
import traceback
import json
from pathlib import Path
import logging
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen


class WindowAPI:
    """Small native bridge; never expose arbitrary path writes to web content."""
    def __init__(self, native_frame: bool = True):
        self.native_frame = native_frame
        self.window = None
        self.is_maximized = False
        self._restore_bounds = None

    def minimize(self):
        if self.window:
            self.window.minimize()

    def toggle_maximize(self):
        if not self.window:
            return
        import sys
        if sys.platform == 'win32' and not self.native_frame:
            # WinForms's maximize() on a frameless form may hide the taskbar.
            # Move this window to its own monitor's work area using native pixels.
            from backend.windows_workarea import toggle_work_area
            native = self.window.native
            hwnd = int(native.Handle.ToInt64())
            self._restore_bounds = toggle_work_area(hwnd, self._restore_bounds)
            self.is_maximized = self._restore_bounds is not None
        else:
            if self.is_maximized:
                self.window.restore()
            else:
                self.window.maximize()
            self.is_maximized = not self.is_maximized

    def close(self):
        if self.window:
            self.window.destroy()

    def save_text(self, filename: str, body: str) -> bool:
        import webview
        if self.window is None:
            return False
        name = re.sub(r'[^a-zA-Z0-9а-яА-ЯёЁ._ -]', '_', filename)[:100]
        if not name.endswith(('.txt', '.srt')):
            raise ValueError('Сохранение поддерживает только TXT и SRT')
        if len(body) > 30_000_000:
            raise ValueError('Текст слишком большой')
        selected = self.window.create_file_dialog(
            webview.SAVE_DIALOG, save_filename=name,
            file_types=('Text files (*.txt;*.srt)', 'All files (*.*)'),
        )
        if not selected:
            return False
        from pathlib import Path
        destination = selected[0] if isinstance(selected, (list, tuple)) else selected
        Path(destination).write_text(body, encoding='utf-8-sig')
        return True


def _log_directory() -> Path:
    """Portable logs live next to EXE; installed logs live in LOCALAPPDATA.

    Never require the portable directory to be writable: e.g. a USB drive or
    extraction under Program Files may be read-only.
    """
    override = os.environ.get('GIGASCRIBE_LOG_DIR')
    if override:
        base = Path(override)
        base.mkdir(parents=True, exist_ok=True)
        return base
    if getattr(sys, 'frozen', False):
        directory = Path(sys.executable).parent
        if (directory / '.portable').is_file():
            try:
                dest = directory / 'logs'
                dest.mkdir(parents=True, exist_ok=True)
                with (dest / '.write-check').open('w', encoding='utf-8') as probe:
                    probe.write('ok')
                (dest / '.write-check').unlink(missing_ok=True)
                return dest
            except OSError:
                pass
    base = Path(os.environ.get('LOCALAPPDATA') or (Path.home() / 'AppData' / 'Local'))
    dest = base / 'GigaScribe' / 'logs'
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def _configure_logging(log_name: str = 'startup.log') -> Path | None:
    """Start a real log before importing the UI/server or checking models."""
    try:
        from logging.handlers import RotatingFileHandler
        folder = _log_directory()
        log_path = folder / log_name
        handler = RotatingFileHandler(log_path, maxBytes=2_000_000,
                                      backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s [%(process)d] %(levelname)s %(name)s: %(message)s'))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        logging.info('Starting %s, executable=%s, frozen=%s, log=%s',
                     log_name, sys.executable, bool(getattr(sys, 'frozen', False)), log_path)
        return log_path
    except Exception:
        return None


def _command(*args: str) -> list[str]:
    """Use the same bundled executable as backend, not an external Python."""
    if getattr(sys, 'frozen', False):
        return [sys.executable, *args]
    return [sys.executable, str(Path(__file__).resolve()), *args]


def _port() -> int:
    """Choose an available loopback port for our short-lived child process."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(('127.0.0.1', 0))
        return listener.getsockname()[1]


def _launch_backend(port: int, token: str, log_dir: Path,
                    models_dir: Path | None = None) -> subprocess.Popen:
    env = os.environ.copy()
    env.update({
        'GIGASCRIBE_SERVER_PORT': str(port),
        'GIGASCRIBE_DESKTOP_TOKEN': token,
        'GIGASCRIBE_LOG_DIR': str(log_dir),
        'PYTHONUTF8': '1',
        'PYINSTALLER_RESET_ENVIRONMENT': '1' if getattr(sys, 'frozen', False) else '0',
    })
    if models_dir is not None:
        env['GIGASCRIBE_MODELS_DIR'] = str(models_dir)
    # The child writes its structured events to backend.log. This separate
    # output file catches early exceptions BEFORE that logger is configured.
    output = (log_dir / 'backend-output.log').open('a', encoding='utf-8')
    try:
        proc = subprocess.Popen(
            _command('--backend'),
            env=env, stdin=subprocess.DEVNULL,
            stdout=output, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0),
            close_fds=True,
        )
    finally:
        output.close()
    logging.info('Spawned backend process pid=%s port=%s', proc.pid, port)
    return proc


def _request_json(url: str, timeout: float = 0.5) -> dict:
    with urlopen(url, timeout=timeout) as response:
        if response.status != 200:
            raise RuntimeError(f'HTTP {response.status}: {url}')
        return json.load(response)


def _wait_for_backend(proc: subprocess.Popen, port: int, seconds: float = 25.0) -> dict:
    deadline = time.monotonic() + seconds
    url = f'http://127.0.0.1:{port}/api/health'
    last_error = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f'Backend process exited with code {proc.returncode}; see backend-output.log')
        try:
            result = _request_json(url, timeout=0.35)
            if result.get('status') == 'ok':
                logging.info('Backend ready, pid=%s', proc.pid)
                return result
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            last_error = exc
        time.sleep(0.12)
    raise TimeoutError(f'Backend did not respond after {seconds:.0f}s; last error: {last_error}')


def _stop_backend(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
    logging.info('Backend terminated: %s', proc.returncode)


def run_backend() -> None:
    """Heavy dependencies and model scans can only run in this child process."""
    _configure_logging('backend.log')
    logging.info('Backend import beginning')
    import uvicorn
    from backend.main import app
    app.state.desktop_token = os.environ.get('GIGASCRIBE_DESKTOP_TOKEN')
    port = int(os.environ['GIGASCRIBE_SERVER_PORT'])
    logging.info('Backend serving on 127.0.0.1:%d', port)
    uvicorn.run(app, host='127.0.0.1', port=port,
                log_level='warning', access_log=False, lifespan='off')


def main(native_frame: bool = True) -> None:
    """GUI process NEVER imports FastAPI, GigaAM, pyannote or PyTorch."""
    log_dir = _log_directory()
    token = secrets.token_urlsafe(32)
    port = _port()
    proc = _launch_backend(port, token, log_dir)
    try:
        _wait_for_backend(proc, port)
        logging.info('Importing native WebView toolkit')
        import webview
        webview.settings['ALLOW_DOWNLOADS'] = True
        url = f'http://127.0.0.1:{port}/?token={token}&chrome={"native" if native_frame else "custom"}'
        bridge = WindowAPI(native_frame=native_frame)
        bridge.window = webview.create_window(
            'GigaScribe', url, js_api=bridge,
            width=1280, height=840, min_size=(930, 690),
            frameless=not native_frame, easy_drag=not native_frame,
            shadow=True, background_color='#15171c',
        )
        if hasattr(bridge.window, 'events'):
            bridge.window.events.shown += lambda: logging.info('Native window shown')
            bridge.window.events.loaded += lambda: logging.info('WebView frontend loaded')
        logging.info('Native window configured, entering GUI event loop')
        webview.start(debug=False)
        logging.info('GUI event loop exited')
    finally:
        _stop_backend(proc)


def smoke_backend() -> int:
    """Exercise actual frozen EXE as HTTP subprocess in CI, without model weights."""
    log_dir = _log_directory()
    try:
        with tempfile.TemporaryDirectory(prefix='gigascribe-smoke-models-') as temporary:
            # Assert that the packaged build detects an already-present model,
            # not just that it responds with an empty model list.
            from backend.transcription import MODELS
            name = MODELS['russian']
            home = Path(temporary) / name
            home.mkdir(parents=True)
            checkpoint = home / (name + '.ckpt')
            tokenizer = home / (name + '_tokenizer.model')
            checkpoint.write_bytes(b'smoke-checkpoint' * 100)
            tokenizer.write_bytes(b'smoke-tokenizer' * 100)
            (home / '.ready.json').write_text(json.dumps({
                checkpoint.name: checkpoint.stat().st_size,
                tokenizer.name: tokenizer.stat().st_size,
            }), encoding='utf-8')
            port = _port()
            token = secrets.token_urlsafe(24)
            proc = _launch_backend(port, token, log_dir, models_dir=Path(temporary))
            try:
                _wait_for_backend(proc, port)
                url = f'http://127.0.0.1:{port}/api/models'
                snapshot = _request_json(url, timeout=3)
                if 'models' not in snapshot or 'vad' not in snapshot:
                    raise AssertionError(f'Invalid model inventory: {snapshot}')
                # The actual disk read is asynchronous. Verify the worker also
                # returns the final state, not only an initial placeholder.
                deadline = time.monotonic() + 10
                while snapshot.get('checking') and time.monotonic() < deadline:
                    time.sleep(0.2)
                    snapshot = _request_json(url, timeout=3)
                if snapshot.get('checking'):
                    raise TimeoutError('Model scan remained pending with an empty local folder')
                assert snapshot['models']['russian']['installed'], 'Packaged server did not discover local model files'
                logging.info('Backend smoke succeeded with %d choices', len(snapshot['models']))
                return 0
            finally:
                _stop_backend(proc)
    except Exception:
        logging.exception('Backend smoke failed')
        return 1


def self_check() -> int:
    """Offline smoke test executed against the actual PyInstaller EXE on Windows.

    This checks bundled imports, frontend files, ffmpeg conversion, and our
    PCM-to-GigaAM adapter. It deliberately downloads no model weights.
    """
    import tempfile
    import traceback
    import wave
    from pathlib import Path

    log = Path(tempfile.gettempdir()) / 'gigascribe-desktop-self-check.log'
    try:
        import torch
        import gigaam
        import pyannote.audio
        # The checkpoint dynamically imports its architecture when loaded.
        # Check the *packaged* executable, not just top-level pyannote.audio.
        from pyannote.audio.models.segmentation.PyanNet import PyanNet
        from pyannote.audio.models.blocks.sincnet import SincNet
        assert issubclass(PyanNet, pyannote.audio.Model), 'VAD architecture is unavailable'
        assert SincNet is not None, 'VAD support block is unavailable'
        import torchcodec
        from backend import vad_store
        from backend.main import DIST
        from backend.transcription import (
            SAMPLE_RATE, ffmpeg_executable, convert_to_wav,
            _use_normalized_wav_loader,
        )
        assert callable(gigaam.load_model), 'GigaAM entry point missing'
        assert torch.version.cuda is None, 'Unexpected CUDA PyTorch: expected CPU-only distribution'
        assert callable(vad_store.installed), 'VAD manager import failed'
        assert (DIST / 'index.html').is_file(), 'Bundled frontend is missing'
        assert (DIST / 'assets').is_dir(), 'Mantine asset files missing'
        assert ffmpeg_executable(), 'Bundled FFmpeg is missing'

        class FakeModel:
            _device = torch.device('cpu')
            _dtype = torch.float32
        fake = FakeModel()
        _use_normalized_wav_loader(fake)
        with tempfile.TemporaryDirectory(prefix='gigascribe-check-') as d:
            src, dst = Path(d) / 'test.wav', Path(d) / 'converted.wav'
            with wave.open(str(src), 'wb') as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(b'\x00\x00' * SAMPLE_RATE)
            convert_to_wav(src, dst)
            audio, length = fake.prepare_wav(str(dst))
            assert audio.shape == (1, SAMPLE_RATE)
            assert length.item() == SAMPLE_RATE
            assert torch.count_nonzero(audio).item() == 0
        log.write_text('OK: audio pipeline and bundled imports are available\n', encoding='utf-8')
        return 0
    except Exception:
        log.write_text(traceback.format_exc(), encoding='utf-8')
        return 1


if __name__ == '__main__':
    if '--self-check' in sys.argv:
        sys.exit(self_check())
    if '--backend' in sys.argv:
        run_backend()
        sys.exit(0)
    log = _configure_logging()
    if '--backend-smoke' in sys.argv:
        sys.exit(smoke_backend())
    try:
        main(native_frame='--custom-frame' not in sys.argv)
    except Exception:
        logging.exception('Desktop startup failed')
        if sys.platform == 'win32':
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    None,
                    'GigaScribe could not start. Diagnostic log:\n' + str(log or '(unavailable)'),
                    'GigaScribe - startup error', 0x10)
            except Exception:
                pass
        raise
