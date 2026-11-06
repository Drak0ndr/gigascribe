"""Regressions for Windows packaging scripts. Does not build an EXE on Linux."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_build_script_safe_for_windows_powershell_codepages():
    # PowerShell 5.1 decodes BOM-less non-ASCII source as ANSI.
    script = (ROOT / "windows" / "build-windows.ps1").read_bytes()
    assert script.isascii()
    assert b"function Run" in script
    assert b"LASTEXITCODE" in script


def test_workflow_runs_pwsh_and_parses_before_installing_dependencies():
    workflow = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text(encoding="utf-8")
    assert "shell: pwsh" in workflow
    assert "Language.Parser]::ParseFile" in workflow
    assert workflow.index("ParseFile") < workflow.index("Install Inno Setup")
    assert "windows\\build-windows.ps1" in workflow


def test_inno_setup_script_has_explicit_utf8_bom():
    assert (ROOT / "windows" / "GigaScribe.iss").read_bytes().startswith(b"\xef\xbb\xbf")


def test_separate_artifacts_and_cpu_build():
    workflow=(ROOT / '.github/workflows/build-windows.yml').read_text(encoding="utf-8")
    script=(ROOT / 'windows/build-windows.ps1').read_text(encoding="utf-8")
    assert 'GigaScribe-Windows-Installer' in workflow
    assert 'GigaScribe-Windows-Portable' in workflow
    assert 'frontend' in workflow and 'npm run build' in workflow
    assert '/whl/cpu' in script and 'torch==2.10.0' in script
    assert '/whl/cu128' not in script
    assert 'gigaam[longform]' in script


def test_frontend_native_export_and_mantine_sources():
    source=(ROOT/'frontend/src/App.tsx').read_text(encoding="utf-8")
    pkg=(ROOT/'frontend/package.json').read_text(encoding="utf-8")
    desktop=(ROOT/'desktop.py').read_text(encoding="utf-8")
    assert '@mantine/core' in pkg
    assert 'pywebview.api.save_text' in source
    assert 'webview.SAVE_DIALOG' in desktop
    assert 'frameless=not native_frame' in desktop
    assert "main(native_frame='--custom-frame' not in sys.argv)" in desktop


def test_vad_checkpoint_architecture_is_bundled_and_checked():
    """Prevent successful EXE packaging with missing dynamically loaded VAD modules."""
    spec = (ROOT / 'GigaScribe.spec').read_text(encoding='utf-8')
    desktop = (ROOT / 'desktop.py').read_text(encoding='utf-8')
    script = (ROOT / 'windows/build-windows.ps1').read_text(encoding='utf-8')
    assert "collect_submodules('pyannote.audio.models.segmentation')" in spec
    assert "collect_submodules('pyannote.audio.models.blocks')" in spec
    assert 'pyannote.audio.models.segmentation.PyanNet' in spec
    assert 'from pyannote.audio.models.segmentation.PyanNet import PyanNet' in desktop
    assert 'from pyannote.audio.models.blocks.sincnet import SincNet' in desktop
    assert 'from pyannote.audio.models.segmentation.PyanNet import PyanNet' in script
    assert script.index('from pyannote.audio.models.segmentation.PyanNet') < script.index("'PyInstaller'")
    assert '& $exe --self-check' in script
    assert 'Largest bundled files:' in script


def test_no_nested_portable_zip_and_marker_uploaded():
    """Upload-artifact makes its own ZIP; hidden marker keeps portable storage."""
    script = (ROOT/'windows/build-windows.ps1').read_text(encoding='utf-8')
    workflow = (ROOT/'.github/workflows/build-windows.yml').read_text(encoding='utf-8')
    assert 'Compress-Archive' not in script
    assert "Set-Content -LiteralPath (Join-Path $portable '.portable')" in script
    assert 'path: portable/GigaScribe/' in workflow
    assert 'include-hidden-files: true' in workflow
    assert 'path: releases/GigaScribe-Windows-Portable.zip' not in workflow
    assert 'path: releases/GigaScribe-Setup-Windows-x64.exe' in workflow


def test_ffmpeg_not_duplicated_and_cpu_runtime():
    spec = (ROOT/'GigaScribe.spec').read_text(encoding='utf-8')
    build = (ROOT/'windows/build-windows.ps1').read_text(encoding='utf-8')
    assert "windows' / 'vendor' / 'ffmpeg.exe'" not in spec
    assert 'binaries=[]' in spec
    assert 'shutil.copyfile(imageio_ffmpeg.get_ffmpeg_exe()' not in build
    assert '/whl/cpu' in build  # CPU-only build.
    assert '--self-check' in build


def test_health_requires_no_torch_import_on_startup(monkeypatch):
    from backend import main
    from fastapi.testclient import TestClient
    import sys
    original = sys.modules.get('torch')
    assert TestClient(main.app).get('/api/health').status_code == 200
    health = TestClient(main.app).get('/api/health').json()
    assert health['device']['cuda_available'] is False
    assert sys.modules.get('torch') is original


def test_frameless_window_uses_current_monitor_work_area():
    from backend.windows_workarea import toggle_work_area, RECT, MONITORINFO
    import ctypes

    class FakeUser32:
        def __init__(self):
            self.positions = []

        def GetWindowRect(self, hwnd, rect):
            obj = ctypes.cast(rect, ctypes.POINTER(RECT)).contents
            obj.left, obj.top, obj.right, obj.bottom = 2100, 150, 3100, 850
            return True

        def MonitorFromWindow(self, hwnd, flag):
            assert flag == 2
            return 42

        def GetMonitorInfoW(self, monitor, ptr):
            assert monitor == 42
            info = ctypes.cast(ptr, ctypes.POINTER(MONITORINFO)).contents
            # Secondary display starts at X=1920, taskbar is on its bottom 48px.
            info.rcWork = RECT(1920, 0, 3840, 1032)
            return True

        def SetWindowPos(self, hwnd, insert_after, x, y, width, height, flags):
            self.positions.append((x,y,width,height,flags))
            return True

    win = FakeUser32()
    restore = toggle_work_area(123, None, user32=win)
    assert restore == (2100, 150, 1000, 700)
    assert win.positions[0][:4] == (1920, 0, 1920, 1032)
    assert toggle_work_area(123, restore, user32=win) is None
    assert win.positions[1][:4] == (2100, 150, 1000, 700)


def test_frontend_offers_four_models_and_cpu_only():
    source = (ROOT / 'frontend/src/App.tsx').read_text(encoding='utf-8')
    for key in ("'russian'", "'russian_ctc'", "'multilingual'", "'multilingual_large'"):
        assert key in source
    assert "const device: Device = 'cpu'" in source
    assert 'setDevice(' not in source
    assert 'Проверяем модели' in source  # Avoid false 'not installed' while loading.
    assert 'serviceError' in source


def test_clean_build_env_prevents_reusing_old_cuda_wheels():
    script = (ROOT / 'windows/build-windows.ps1').read_text(encoding='ascii')
    assert "Remove-Item -Recurse -Force '.venv-build'" in script
    assert "torch.version.cuda is None" in script
    assert script.index("Remove-Item -Recurse -Force '.venv-build'") < script.index("'venv', '.venv-build'")
