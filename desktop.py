"""Desktop launcher: dedicated native window + embedded local FastAPI server.

The installer bundles Python, the UI, FFmpeg and the inference runtime.
Model checkpoints are NOT included and are only downloaded on user request.
"""
from __future__ import annotations

import secrets
import socket
import threading
import time
from urllib.error import URLError
from urllib.request import urlopen

import uvicorn


def main() -> None:
    import webview
    from backend.main import app

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

        url = f'http://127.0.0.1:{port}/?token={app.state.desktop_token}'
        webview.create_window('GigaScribe — расшифровка аудио', url,
                              width=1280, height=840, min_size=(920, 680),
                              background_color='#0b1020')
        # Windows WebView2 is available by default on modern Windows 10/11.
        webview.start(debug=False)
    finally:
        server.should_exit = True
        thread.join(timeout=3)
        sock.close()


if __name__ == '__main__':
    main()
