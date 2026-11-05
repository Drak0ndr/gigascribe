"""Desktop launcher: dedicated native window + embedded local FastAPI server.

The installer bundles Python, the UI, FFmpeg and the inference runtime.
Model checkpoints are NOT included and are only downloaded on user request.
"""
from __future__ import annotations

import secrets
import os
import re
import socket
import logging
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn


class WindowAPI:
    """Small native bridge; never expose arbitrary path writes to web content."""
    def __init__(self):
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
        if sys.platform == 'win32':
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


def _configure_logging():
    """Never make logging a prerequisite for opening the desktop window."""
    try:
        from logging.handlers import RotatingFileHandler
        from pathlib import Path
        base = Path(os.environ.get('LOCALAPPDATA') or Path.home() / 'AppData' / 'Local')
        folder = base / 'GigaScribe' / 'logs'
        folder.mkdir(parents=True, exist_ok=True)
        log_path = folder / 'startup.log'
        handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2, encoding='utf-8')
        handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.INFO)
        return log_path
    except Exception:
        return None


def main() -> None:
    import webview
    from backend.main import app
    from pathlib import Path
    import sys
    logging.info('Desktop launch: Python %s, packaged=%s', sys.version.split()[0], bool(getattr(sys, 'frozen', False)))
    # FFmpeg is shipped once by imageio_ffmpeg and accessed using its
    # absolute path in the audio conversion layer. Do not add a second copy.
    webview.settings['ALLOW_DOWNLOADS'] = True

    app.state.desktop_token = secrets.token_urlsafe(32)
    # Reserve a port and make uvicorn use the bound listener to avoid races.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    sock.set_inheritable(True)
    port = sock.getsockname()[1]
    config = uvicorn.Config(app, log_level='warning', access_log=False, lifespan='off')
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={'sockets': [sock]}, daemon=True)
    thread.start()
    try:
        health_url = f'http://127.0.0.1:{port}/api/health'
        for _ in range(100):
            try:
                with urlopen(health_url, timeout=0.25) as response:
                    if response.status == 200:
                        break
            except (URLError, TimeoutError, OSError):
                time.sleep(0.1)
        else:
            raise RuntimeError('Локальный сервер GigaScribe не запустился')

        logging.info('Local HTTP service ready')
        url = f'http://127.0.0.1:{port}/?token={app.state.desktop_token}'
        bridge = WindowAPI()
        bridge.window = webview.create_window('GigaScribe', url, js_api=bridge,
                              width=1280, height=840, min_size=(930, 690),
                              frameless=True, easy_drag=True, shadow=True,
                              background_color='#15171c')
        # Windows WebView2 is available by default on modern Windows 10/11.
        logging.info('Desktop window created; entering WebView event loop')
        webview.start(debug=False)
        logging.info('Desktop window closed')
    finally:
        server.should_exit = True
        thread.join(timeout=3)
        sock.close()


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
    import sys
    if '--self-check' in sys.argv:
        sys.exit(self_check())
    log = _configure_logging()
    try:
        main()
    except Exception:
        logging.exception('Desktop startup failed')
        # In a windowed PyInstaller build stdout/stderr are invisible.
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
