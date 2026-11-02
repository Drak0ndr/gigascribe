# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir Windows build; run only on Windows with Python 3.11."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH)
static = root / 'frontend' / 'dist'
datas = [
    (str(static / 'index.html'), 'frontend/dist'),
    (str(static / 'app.js'), 'frontend/dist'),
    (str(static / 'styles.css'), 'frontend/dist'),
]
datas += collect_data_files('gigaam')
datas += collect_data_files('imageio_ffmpeg')
# Dynamically instantiated Hydra components and CTC/RNNT heads from checkpoints.
hiddenimports = collect_submodules('gigaam') + [
    'clr', 'pythonnet', 'webview.platforms.winforms', 'webview.platforms.edgechromium',
    'hydra', 'omegaconf', 'soundfile', 'sentencepiece', 'imageio_ffmpeg',
    'uvicorn.protocols.http.h11_impl', 'uvicorn.lifespan.off', 'uvicorn.loops.asyncio',
]
a = Analysis(
    [str(root / 'desktop.py')], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='GigaScribe',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               upx_exclude=[], name='GigaScribe')
