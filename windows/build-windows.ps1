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
$pythonVersion = (& python -c 'import sys; print(".".join(map(str, sys.version_info[:2])))').Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne '3.11') { throw 'Python 3.11 is required' }
Run -Command 'python' -ArgsList @('-m', 'venv', '.venv-build')
$python = Join-Path $root '.venv-build\Scripts\python.exe'
Run -Command $python -ArgsList @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel')
# RTX 50xx (Blackwell) requires a CUDA build; CPU-only wheels were a v1.2 mistake.
# 2.10/torchcodec 0.10 satisfy upstream GigaAM's longform extra.
Run -Command $python -ArgsList @('-m', 'pip', 'install', 'torch==2.10.0', 'torchaudio==2.10.0', '--index-url', 'https://download.pytorch.org/whl/cu128')
Run -Command $python -ArgsList @('-m', 'pip', 'install', '-r', 'requirements-desktop.txt', 'pyinstaller', 'pytest', 'httpx', 'gigaam[longform] @ git+https://github.com/salute-developers/GigaAM.git')
Run -Command $python -ArgsList @('-m', 'pip', 'check')
Run -Command $python -ArgsList @('-m', 'pytest', 'tests', '-q')
# PyInstaller destinations are directories, so stage with canonical filename.
Run -Command $python -ArgsList @('-c', 'import imageio_ffmpeg,shutil,pathlib; p=pathlib.Path("windows/vendor"); p.mkdir(parents=True,exist_ok=True); shutil.copyfile(imageio_ffmpeg.get_ffmpeg_exe(),p/"ffmpeg.exe")')
Run -Command $python -ArgsList @('-m', 'pip', 'cache', 'purge')
Run -Command $python -ArgsList @('-c', 'import torch; assert torch.version.cuda, "CUDA wheel missing"; print("CUDA runtime:", torch.version.cuda)')
Run -Command $python -ArgsList @('-m', 'PyInstaller', '--noconfirm', '--clean', 'GigaScribe.spec')
$exe = Join-Path $root 'dist\GigaScribe\GigaScribe.exe'
if (-not (Test-Path $exe)) { throw 'PyInstaller did not create GigaScribe.exe' }
# Model weights intentionally excluded. The self-check imports bundled VAD dependencies.
& $exe --self-check
if ($LASTEXITCODE -ne 0) {
    $log = Join-Path $env:TEMP 'gigascribe-desktop-self-check.log'
    if (Test-Path $log) { Get-Content $log }
    throw 'Packaged EXE self-check failed'
}
$portable = Join-Path $root 'portable\GigaScribe'
if (Test-Path $portable) { Remove-Item -Recurse -Force $portable }
New-Item -ItemType Directory -Path $portable -Force | Out-Null
Copy-Item -Path (Join-Path $root 'dist\GigaScribe\*') -Destination $portable -Recurse -Force
New-Item -ItemType File -Path (Join-Path $portable '.portable') -Force | Out-Null
$releases = Join-Path $root 'releases'
New-Item -ItemType Directory -Path $releases -Force | Out-Null
Compress-Archive -Path $portable -DestinationPath (Join-Path $releases 'GigaScribe-Windows-Portable.zip') -Force
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
