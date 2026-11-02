# GigaScribe Desktop v1.3.0

Приложение для локальной расшифровки аудио на GigaAM от Сбера. Новая версия: React + **Mantine**, окно без стандартного Windows titlebar, выбор вычислительного устройства, два режима обработки длинных записей, отдельный опциональный VAD, системный диалог сохранения TXT/SRT.

## Как получить EXE через GitHub Actions

1. Замените файлы в репозитории содержимым ZIP. В том числе `.github/workflows/build-windows.yml`.
2. Сделайте commit и push в GitHub.
3. Откройте **Actions → Build GigaScribe for Windows → Run workflow**.
4. После зелёной отметки раскройте **Artifacts**. Теперь публикуются **ДВА независимых артефакта**:
   - `GigaScribe-Windows-Installer` → архив с `GigaScribe-Setup-Windows-x64.exe`.
   - `GigaScribe-Windows-Portable` → архив с `GigaScribe-Windows-Portable.zip`.
5. Для установленной версии запустите Setup.exe. Для переносной распакуйте внутренний ZIP и запустите `GigaScribe.exe` из папки `GigaScribe` (не выносите EXE отдельно из папки с библиотеками).

**Важно:** в этом архиве исходники, не готовый Windows EXE. В текущей Linux-среде Windows-сборка/работа с реальными весами не проверены. CUDA-сборка содержит большой PyTorch и может производить крупные загрузки/артефакты. GitHub Actions должен собрать React интерфейс командой `npm run build` **до** PyInstaller.

## Модели

Во вкладке **Модели**:
- `GigaAM v3_e2e_rnnt` — русский с пунктуацией;
- `GigaAM multilingual_ctc` — мультиязычное распознавание;
- **VAD `pyannote/segmentation-3.0`** — отдельно, *необязательный* компонент для длинных файлов.

Ни одна модель не скачивается автоматически. Система хранит веса на диске при перезапуске. Каждую можно удалить отдельно.

В установленной Windows-версии: `%LOCALAPPDATA%\GigaScribe\models`. В Portable: `models` рядом с `GigaScribe.exe`. При запуске исходников: `./models`. Для VAD используются файлы `models/vad/weights`.

**VAD:** получите read-токен на https://huggingface.co/settings/tokens и примите условия на https://huggingface.co/pyannote/segmentation-3.0 . Введите токен в поле приложения и нажмите «Скачать VAD». Токен используется только для фонового скачивания и **не сохраняется** программой. Веса VAD после загрузки работают локально. VAD определяет границы речи, но не гарантирует исправления всех ошибок модели распознавания.

Режимы распознавания: стандартное разбиение на фрагменты по паузам либо официальный **`model.transcribe_longform()`** с VAD. Во втором режиме выполняется пакетная расшифровка с ограниченным batch size 2; индикатор может некоторое время оставаться на одном значении во время вычисления — API GigaAM не предоставляет callback по каждому батчу.

## Почему RTX 5060 раньше не использовалась

Версия 1.2.2 в build-windows.ps1 явно ставила PyTorch из `https://download.pytorch.org/whl/cpu`. На GPU ничего не запускалось. В 1.3.0 используется **PyTorch 2.10.0 + CUDA 12.8** из официального индекса. В настройках показаны CUDA, GPU и версия PyTorch. В рабочей области можно выбрать `Автоматически`, `NVIDIA CUDA` или `CPU`. При явно выбранной NVIDIA без доступной CUDA приложение выдаёт ошибку, а не притворяется, что использует GPU.

Для CUDA требуется совместимый драйвер NVIDIA. Если видеокарта определяется, но ядра запускаются с ошибкой совместимости, проверьте актуальность драйвера и сборку PyTorch. Наличие CUDA *не означает* нулевую загрузку CPU: декодирование/конвертация и часть работы по-прежнему CPU.

## Как локально запустить (Windows, разработка)

Установите Python 3.11, Git, Node.js 22, драйвер NVIDIA (если есть RTX). В PowerShell из корня проекта:

```powershell
cd frontend
npm install
npm run build
cd ..
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -U pip
.\.venv\Scripts\python.exe -m pip install torch==2.10.0 torchaudio==2.10.0 --index-url https://download.pytorch.org/whl/cu128
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt "gigaam[longform] @ git+https://github.com/salute-developers/GigaAM.git"
.\.venv\Scripts\python.exe desktop.py
```

Затем достаточно дважды нажимать `start-desktop.bat`. Для проверки API/тестов: `python -m pytest tests -q` (в выбранном Python окружении).

Чтобы вручную собрать установщик на Windows, установите Inno Setup 6 и PyInstaller и используйте `powershell -ExecutionPolicy Bypass -File windows\build-windows.ps1`. Сборочный скрипт создаёт своё окружение `.venv-build`.

## Технические детали

- Python FastAPI слушает только loopback-интерфейс, а изменения из десктопного окна требуют одноразовый X-GigaScribe-Token.
- При установленной версии на Windows TXT/SRT сохраняется через системный диалог `pywebview.SAVE_DIALOG`, с UTF-8 BOM (для Блокнота). В браузерной dev-версии — обычная загрузка через Blob.
- GigaAM — внешний проект, модели и их лицензии определяются владельцем. Код GigaScribe не включает веса в установщик.
- Реальный инференс на RTX 5060, совместимость `pyannote.audio`/`torchcodec` с Windows PyInstaller и поведение рамки на конкретной машине требуют smoke-test **на Windows**, а не только pytest на Linux. PyInstaller `--self-check` добавлен в пайплайн до загрузки артефактов, но без весов.
- Установка нового установщика поверх старого не удаляет `models` в `%LOCALAPPDATA%`.
