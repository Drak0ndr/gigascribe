# ASCII-only PowerShell script: compatible with Windows PowerShell and pwsh.
$ErrorActionPreference = 'Stop'
# Use UTF-8 for Python text I/O regardless of the Windows console ANSI code page.
$env:PYTHONUTF8 = '1'
Set-StrictMode -Version Latest
if ($env:OS -ne 'Windows_NT') { throw 'Windows only' }
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $root
function Run {
    param([string]$Command, [string[]]$ArgsList)
    & $Command @ArgsList
    if ($LASTEXITCODE -ne 0) { throw "Failed (${LASTEXITCODE}): $Command $($ArgsList -join ' ')" }
}
if (-not (Test-Path 'frontend\dist\index.html')) { throw 'Missing Mantine frontend; run npm run build inside frontend first.' }
# Always recreate the venv: old CUDA packages must never leak into Lite builds.
if (Test-Path '.venv-build') { Remove-Item -Recurse -Force '.venv-build' }
$pythonVersion = (& python -c 'import sys; print(".".join(map(str, sys.version_info[:2])))').Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne '3.11') { throw 'Python 3.11 is required' }
Run -Command 'python' -ArgsList @('-m', 'venv', '.venv-build')
$python = Join-Path $root '.venv-build\Scripts\python.exe'
Run -Command $python -ArgsList @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel')
# CPU-only wheels dramatically reduce the download and PyInstaller bundle size.
# Explicitly use the CPU index; do not allow pip to select a CUDA wheel.
Run -Command $python -ArgsList @('-m', 'pip', 'install', 'torch==2.10.0', 'torchaudio==2.10.0', '--index-url', 'https://download.pytorch.org/whl/cpu')
Run -Command $python -ArgsList @('-m', 'pip', 'install', '-r', 'requirements-desktop.txt', 'pyinstaller', 'pytest', 'httpx', 'gigaam[longform] @ git+https://github.com/salute-developers/GigaAM.git')
Run -Command $python -ArgsList @('-m', 'pip', 'check')
# Fail *before* the expensive PyInstaller build if the expected checkpoint
# architecture cannot be imported from the installed pyannote distribution.
Run -Command $python -ArgsList @('-c', 'from pyannote.audio.models.segmentation.PyanNet import PyanNet; from pyannote.audio.models.blocks.sincnet import SincNet; print("VAD checkpoint modules:", PyanNet.__module__, SincNet.__module__)')
Run -Command $python -ArgsList @('-m', 'pytest', 'tests', '-q')
# Use imageio-ffmpeg's own bundled executable. No second 100-MiB copy.
Run -Command $python -ArgsList @('-m', 'pip', 'cache', 'purge')
Run -Command $python -ArgsList @('-c', 'import torch; assert torch.version.cuda is None, "CUDA wheel accidentally installed"; print("CPU-only PyTorch:", torch.__version__)')
Run -Command $python -ArgsList @('-m', 'PyInstaller', '--noconfirm', '--clean', 'GigaScribe.spec')
$exe = Join-Path $root 'dist\GigaScribe\GigaScribe.exe'
if (-not (Test-Path $exe)) { throw 'PyInstaller did not create GigaScribe.exe' }
# Report the real disk consumers rather than guessing which CUDA DLLs are safe
# to remove.  This does not change runtime files.
$distRoot = Join-Path $root 'dist\GigaScribe'
$files = @(Get-ChildItem -LiteralPath $distRoot -Recurse -File)
$bytes = ($files | Measure-Object -Property Length -Sum).Sum
Write-Host ('Portable uncompressed size: {0:N2} GiB' -f ($bytes / 1GB))
Write-Host 'Largest bundled files:'
$files | Sort-Object Length -Descending | Select-Object -First 20 | ForEach-Object {
    Write-Host ('  {0,8:N1} MiB  {1}' -f ($_.Length / 1MB), $_.FullName.Substring($distRoot.Length + 1))
}
# Model weights intentionally excluded. The self-check imports bundled VAD dependencies.
& $exe --self-check
if ($LASTEXITCODE -ne 0) {
    $log = Join-Path $env:TEMP 'gigascribe-desktop-self-check.log'
    if (Test-Path $log) { Get-Content $log }
    throw 'Packaged EXE self-check failed'
}
# Exercise the actual packed EXE's HTTP backend, model inventory and shutdown.
$env:GIGASCRIBE_LOG_DIR = Join-Path $root 'build-logs'
New-Item -ItemType Directory -Path $env:GIGASCRIBE_LOG_DIR -Force | Out-Null
& $exe --backend-smoke
if ($LASTEXITCODE -ne 0) {
    Get-ChildItem -LiteralPath $env:GIGASCRIBE_LOG_DIR -Filter '*.log' | ForEach-Object {
        Write-Host ('=== ' + $_.Name + ' ===')
        Get-Content -LiteralPath $_.FullName -Tail 100
    }
    throw 'Packaged EXE backend smoke failed'
}
Remove-Item Env:GIGASCRIBE_LOG_DIR
$portable = Join-Path $root 'portable\GigaScribe'
if (Test-Path $portable) { Remove-Item -Recurse -Force $portable }
New-Item -ItemType Directory -Path $portable -Force | Out-Null
Copy-Item -Path (Join-Path $root 'dist\GigaScribe\*') -Destination $portable -Recurse -Force
Set-Content -LiteralPath (Join-Path $portable '.portable') -Value 'portable' -Encoding ascii
# GitHub upload-artifact creates ONE download ZIP from the portable directory.
# Do not create a separate inner ZIP. .portable must be included as a hidden file.
$releases = Join-Path $root 'releases'
New-Item -ItemType Directory -Path $releases -Force | Out-Null
if (-not (Test-Path (Join-Path $portable 'GigaScribe.exe'))) { throw 'Portable EXE missing' }
if (-not (Test-Path (Join-Path $portable '.portable'))) { throw 'Portable storage marker missing' }
$iscc = $null
$cmd = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
if ($cmd) { $iscc = $cmd.Source }
if (-not $iscc) {
    foreach ($p in @(${env:ProgramFiles(x86)}, $env:ProgramFiles) | Where-Object { $_ }) {
        $candidate = Join-Path $p 'Inno Setup 6\ISCC.exe'
        if (Test-Path $candidate) { $iscc = $candidate; break }
    }
}
if (-not $iscc) { throw 'Inno Setup 6 is missing' }
Run -Command $iscc -ArgsList @('windows\GigaScribe.iss')
if (-not (Test-Path (Join-Path $releases 'GigaScribe-Setup-Windows-x64.exe'))) { throw 'Setup EXE missing' }
Get-ChildItem $releases
Write-Host ('Portable ready in: {0}' -f $portable)
