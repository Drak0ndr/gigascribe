# Build GigaScribe for Windows (PowerShell 5.1 / 7 compatible).
# Keep this file ASCII-only to avoid Windows PowerShell encoding problems.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

if ($env:OS -ne 'Windows_NT') {
    throw 'This script must run on Windows.'
}

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $projectRoot

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Executable $($Arguments -join ' ')"
    }
}

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
    throw 'Python 3.11 must be installed and available on PATH.'
}

$pythonVersion = (& python -c 'import sys; print(".".join(map(str, sys.version_info[:2])))').Trim()
if ($LASTEXITCODE -ne 0 -or $pythonVersion -ne '3.11') {
    throw "Python 3.11 required; found $pythonVersion."
}

Invoke-Checked 'python' @('-m', 'venv', '.venv-build')
$python = (Join-Path $projectRoot '.venv-build\Scripts\python.exe')
Invoke-Checked $python @('-m', 'pip', 'install', '--upgrade', 'pip', 'setuptools', 'wheel')
Invoke-Checked $python @('-m', 'pip', 'install', 'torch', 'torchaudio', '--index-url', 'https://download.pytorch.org/whl/cpu')
Invoke-Checked $python @('-m', 'pip', 'install', '-r', 'requirements-desktop.txt', 'pyinstaller', 'pytest', 'httpx', 'gigaam[torch] @ git+https://github.com/salute-developers/GigaAM.git')
Invoke-Checked $python @('-m', 'pytest', 'tests', '-q')
Invoke-Checked $python @('-m', 'PyInstaller', '--noconfirm', '--clean', 'GigaScribe.spec')

$builtExe = Join-Path $projectRoot 'dist\GigaScribe\GigaScribe.exe'
if (-not (Test-Path $builtExe)) {
    throw 'PyInstaller did not produce GigaScribe.exe.'
}

# Make a smoke test against the FROZEN EXE rather than the source Python only.
# Fails the workflow if bundled imports/FFmpeg/PCM loader are broken.
& $builtExe --self-check
if ($LASTEXITCODE -ne 0) {
    $smokeLog = Join-Path $env:TEMP 'gigascribe-desktop-self-check.log'
    if (Test-Path $smokeLog) { Get-Content $smokeLog }
    throw 'Frozen EXE self-check failed. See log above.'
}

$portable = Join-Path $projectRoot 'portable\GigaScribe'
if (Test-Path $portable) { Remove-Item $portable -Recurse -Force }
New-Item -ItemType Directory -Path $portable -Force | Out-Null
Copy-Item -Path (Join-Path $projectRoot 'dist\GigaScribe\*') -Destination $portable -Recurse -Force
New-Item -ItemType File -Path (Join-Path $portable '.portable') -Force | Out-Null
$releases = Join-Path $projectRoot 'releases'
New-Item -ItemType Directory -Path $releases -Force | Out-Null
Compress-Archive -Path $portable -DestinationPath (Join-Path $releases 'GigaScribe-Windows-Portable.zip') -Force

$isccCommand = Get-Command 'ISCC.exe' -ErrorAction SilentlyContinue
$isccPath = if ($isccCommand) { $isccCommand.Source } else { $null }
if (-not $isccPath) {
    $candidateRoots = @(${env:ProgramFiles(x86)}, $env:ProgramFiles) | Where-Object { $_ }
    foreach ($root in $candidateRoots) {
        $candidate = Join-Path $root 'Inno Setup 6\ISCC.exe'
        if (Test-Path $candidate) { $isccPath = $candidate; break }
    }
}
if (-not $isccPath) { throw 'Inno Setup 6 (ISCC.exe) is required to build the installer.' }
Invoke-Checked $isccPath @('windows\GigaScribe.iss')

$setup = Join-Path $releases 'GigaScribe-Setup-Windows-x64.exe'
if (-not (Test-Path $setup)) { throw 'Inno Setup did not produce the installer.' }
Get-ChildItem $releases
