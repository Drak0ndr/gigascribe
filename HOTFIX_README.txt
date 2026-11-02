GigaScribe v1.3.2 Windows UnicodeDecodeError Hotfix

Unzip this archive at your GigaScribe GitHub repository root, preserving directories and overwriting files.
Commit and push; rerun GitHub Actions > Build GigaScribe for Windows.
Files changed:
  tests/test_windows_distribution.py (explicit UTF-8 for all read_text calls)
  tests/test_backend.py (explicit UTF-8 for test fixture and log files)
  windows/build-windows.ps1 (sets PYTHONUTF8=1)
The model manager, saved models, UI and inference logic are unchanged.

Validated: 28 tests passed under Linux Python 3.13; real Windows builder validation pending GitHub Actions.
