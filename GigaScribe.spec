# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller onedir, CUDA-enabled Windows build with Mantine frontend."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

root = Path(SPECPATH)
static = root / 'frontend' / 'dist'
if not (static / 'index.html').exists():
    raise FileNotFoundError('Build Mantine frontend first: cd frontend && npm run build')
datas = [(str(p), str(p.parent.relative_to(root))) for p in static.rglob('*') if p.is_file()]
datas += collect_data_files('gigaam')
datas += collect_data_files('pyannote.audio')
datas += collect_data_files('imageio_ffmpeg')
# Upstream VAD uses runtime imports; PyInstaller needs PyTorch/TorchCodec native libraries.
# Pyannote checkpoint contains a dynamically resolved architecture path such as
# `pyannote.audio.models.segmentation.PyanNet`.  A normal import of pyannote.audio
# does not pull these modules into PyInstaller's dependency graph.
# Include only model architecture and support blocks rather than collecting the
# entire pyannote.audio package, to avoid unnecessarily bloating the archive.
pyannote_model_imports = (
    collect_submodules('pyannote.audio.models.segmentation')
    + collect_submodules('pyannote.audio.models.blocks')
)
if 'pyannote.audio.models.segmentation.PyanNet' not in pyannote_model_imports:
    raise RuntimeError('Pyannote PyanNet architecture is missing from build environment')
hiddenimports = collect_submodules('gigaam') + pyannote_model_imports + [
    'clr', 'pythonnet', 'webview.platforms.winforms', 'webview.platforms.edgechromium',
    'hydra', 'omegaconf', 'soundfile', 'sentencepiece', 'imageio_ffmpeg',
    'pyannote.audio', 'pyannote.core', 'pyannote.pipeline', 'torchcodec',
    'huggingface_hub', 'uvicorn.protocols.http.h11_impl',
    'uvicorn.lifespan.off', 'uvicorn.loops.asyncio',
]
a = Analysis(
    [str(root / 'desktop.py')], pathex=[str(root)],
    # Second binary is a stable unversioned executable for upstream GigaAM/torchcodec tools.
    binaries=[(str(root / 'windows' / 'vendor' / 'ffmpeg.exe'), 'tools')],
    datas=datas, hiddenimports=hiddenimports, hookspath=[], hooksconfig={},
    runtime_hooks=[], excludes=['PyQt5', 'PyQt6', 'PySide2', 'PySide6', 'tkinter'],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='GigaScribe',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False,
               upx_exclude=[], name='GigaScribe')
