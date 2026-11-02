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
    workflow = (ROOT / ".github" / "workflows" / "build-windows.yml").read_text()
    assert "shell: pwsh" in workflow
    assert "Language.Parser]::ParseFile" in workflow
    assert workflow.index("ParseFile") < workflow.index("Install Inno Setup")
    assert "windows\\build-windows.ps1" in workflow


def test_inno_setup_script_has_explicit_utf8_bom():
    assert (ROOT / "windows" / "GigaScribe.iss").read_bytes().startswith(b"\xef\xbb\xbf")


def test_separate_artifacts_and_gpu_build():
    workflow=(ROOT / '.github/workflows/build-windows.yml').read_text()
    script=(ROOT / 'windows/build-windows.ps1').read_text()
    assert 'GigaScribe-Windows-Installer' in workflow
    assert 'GigaScribe-Windows-Portable' in workflow
    assert 'frontend' in workflow and 'npm run build' in workflow
    assert 'cu128' in script and 'torch==2.10.0' in script
    assert 'gigaam[longform]' in script


def test_frontend_native_export_and_mantine_sources():
    source=(ROOT/'frontend/src/App.tsx').read_text()
    pkg=(ROOT/'frontend/package.json').read_text()
    desktop=(ROOT/'desktop.py').read_text()
    assert '@mantine/core' in pkg
    assert 'pywebview.api.save_text' in source
    assert 'webview.SAVE_DIALOG' in desktop
    assert 'frameless=True' in desktop
