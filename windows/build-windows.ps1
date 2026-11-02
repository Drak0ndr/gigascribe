# Developer-only script: use GitHub Actions for a no-command build workflow.
$ErrorActionPreference = 'Stop'
Set-Location (Resolve-Path "$PSScriptRoot\..")
if ($env:OS -ne 'Windows_NT') { throw 'Сборка EXE возможна только под Windows.' }
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw 'Требуется Python 3.11 для сборки.' }
py -3.11 -m venv .venv-build
$python = '.\.venv-build\Scripts\python.exe'
& $python -m pip install --upgrade pip setuptools wheel
& $python -m pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
& $python -m pip install -r requirements-desktop.txt pyinstaller pytest httpx "git+https://github.com/salute-developers/GigaAM.git"
& $python -m pytest tests -q
& $python -m PyInstaller --noconfirm --clean GigaScribe.spec
if (-not (Test-Path 'dist\GigaScribe\GigaScribe.exe')) { throw 'PyInstaller не создал EXE.' }
$portable = 'portable\GigaScribe'
New-Item -ItemType Directory -Force $portable | Out-Null
Copy-Item -Path 'dist\GigaScribe\*' -Destination $portable -Recurse -Force
New-Item -ItemType File -Force "$portable\.portable" | Out-Null
New-Item -ItemType Directory -Force releases | Out-Null
Compress-Archive -Path $portable -DestinationPath 'releases\GigaScribe-Windows-Portable.zip' -Force
$iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
if (-not $iscc) {
  $fallback = Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'
  if (Test-Path $fallback) { $iscc = @{ Source = $fallback } }
}
if (-not $iscc) { throw 'Установите Inno Setup 6 для сборки инсталлятора.' }
& $iscc.Source windows\GigaScribe.iss
Get-ChildItem releases
