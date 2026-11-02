GigaScribe v1.3.1 frontend hotfix

Unzip in the ROOT of your current GigaScribe repository, preserving folder paths and overwriting files. Commit + push, then rerun Actions -> Build GigaScribe for Windows.

Fixes:
- IconAudioLines -> existing supported IconMicrophone
- typed auth headers Record<string, string>
- Mantine FileButton uses render props for its Button child

No change to Python backend or model cache.
