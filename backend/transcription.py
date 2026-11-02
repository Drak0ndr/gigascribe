"""Local GigaAM inference and bounded, silence-aware WAV segmentation."""

from __future__ import annotations

import gc
import shutil
import threading
import wave
from pathlib import Path
from subprocess import CalledProcessError, TimeoutExpired, run
from typing import Callable

import numpy as np

MODELS = {
    "russian": "v3_e2e_rnnt",
    "multilingual": "multilingual_ctc",
}
SAMPLE_RATE = 16_000
MAX_SEGMENT_SECONDS = 23.0  # GigaAM .transcribe() supports <= 25 seconds.

_model_lock = threading.Lock()
_current_model_name: str | None = None
_current_model = None


def load_model(choice: str):
    """Keep only one model in memory, even when users switch variants."""
    global _current_model, _current_model_name
    if choice not in MODELS:
        raise ValueError("Неизвестная модель")

    with _model_lock:
        name = MODELS[choice]
        if _current_model_name == name and _current_model is not None:
            return _current_model
        _current_model = None
        _current_model_name = None
        gc.collect()
        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        try:
            import gigaam
        except ImportError as exc:
            raise RuntimeError(
                "GigaAM не установлен. Выполните инструкцию установки из README.md."
            ) from exc
        from backend.model_store import installed, folder
        if not installed(choice):
            raise RuntimeError("Сначала скачайте выбранную модель во вкладке «Модель распознавания»")
        model = gigaam.load_model(name, download_root=str(folder(choice)))
        _current_model = model
        _current_model_name = name
        return model


def unload_model(choice: str) -> None:
    """Release the selected model before its files are removed from disk."""
    global _current_model, _current_model_name
    with _model_lock:
        if _current_model_name == MODELS[choice]:
            _current_model = None
            _current_model_name = None
            gc.collect()
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass


def ffmpeg_executable() -> str | None:
    """Use the bundled FFmpeg binary in desktop builds; PATH in development."""
    try:
        import imageio_ffmpeg
        executable = imageio_ffmpeg.get_ffmpeg_exe()
        if Path(executable).is_file():
            return executable
    except (ImportError, RuntimeError, FileNotFoundError):
        pass
    return shutil.which('ffmpeg')


def convert_to_wav(source: Path, target: Path) -> None:
    """Decode audio/video containers into the 16-kHz mono PCM expected by GigaAM."""
    try:
        run(
            [
                ffmpeg_executable() or "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
                "-y", "-i", str(source), "-map", "0:a:0", "-vn",
                "-ac", "1", "-ar", str(SAMPLE_RATE), "-c:a", "pcm_s16le", str(target),
            ],
            check=True, capture_output=True, text=True, timeout=900,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("FFmpeg не найден. Установите FFmpeg и добавьте его в PATH.") from exc
    except CalledProcessError as exc:
        reason = (exc.stderr or "").strip()
        raise ValueError("Не удалось прочитать аудио. Проверьте формат файла. " + reason[-450:]) from exc
    except TimeoutExpired as exc:
        raise RuntimeError("Время конвертации аудио истекло.") from exc


def _quiet_cut(wav: wave.Wave_read, start_frame: int, max_end: int) -> int:
    """Prefer a quiet ~120ms window close to 23s boundary instead of cutting speech.

    If there is no meaningful pause, use the maximum boundary. No overlapping
    segments are created to avoid repeated words in the result.
    """
    sr = wav.getframerate()
    search_start = max(start_frame + sr, max_end - int(2.5 * sr))
    length = max_end - search_start
    if length < int(0.5 * sr):
        return max_end
    wav.setpos(search_start)
    samples = np.frombuffer(wav.readframes(length), dtype="<i2")
    frame_width = int(sr * 0.12)
    if len(samples) < frame_width * 3:
        return max_end
    # Evaluate non-overlapping 120ms windows (excluding the very end).
    usable = (len(samples) // frame_width) * frame_width
    blocks = samples[:usable].astype(np.float32).reshape(-1, frame_width)
    energies = np.sqrt(np.mean(blocks * blocks, axis=1))
    # Pick the quietest plausible pause; a signal with no quiet interval
    # falls back to the ordinary hard boundary.
    candidates = np.where(energies < 360)[0]
    if candidates.size == 0:
        return max_end
    # Prefer the last pause to maximize segment length.
    index = int(candidates[-1])
    cut = search_start + (index + 1) * frame_width
    return cut if cut > start_frame else max_end


def split_wav(wav_path: Path, work_dir: Path) -> list[tuple[float, float, Path]]:
    """Returns ordered, non-overlapping segments with timestamps in seconds."""
    result: list[tuple[float, float, Path]] = []
    with wave.open(str(wav_path), "rb") as wav:
        sr = wav.getframerate()
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or sr != SAMPLE_RATE:
            raise ValueError("Внутренняя ошибка: неверный формат PCM WAV")
        total = wav.getnframes()
        if total == 0:
            raise ValueError("В файле нет аудиоданных")
        if total > sr * 7200:
            raise ValueError("Поддерживаются записи длительностью до 2 часов")

        start = 0
        index = 0
        while start < total:
            end = min(total, start + int(MAX_SEGMENT_SECONDS * sr))
            # Don't create tiny trailing fragments: they can be combined with
            # the preceding chunk if the entire combined span remains <= 25 s.
            if end < total and total - end < 2 * sr:
                end = min(total, start + 24 * sr)
            if end < total and end - start > 3 * sr:
                end = _quiet_cut(wav, start, end)
            wav.setpos(start)
            data = wav.readframes(end - start)
            segment_path = work_dir / f"segment_{index:04d}.wav"
            with wave.open(str(segment_path), "wb") as out:
                out.setnchannels(1)
                out.setsampwidth(2)
                out.setframerate(sr)
                out.writeframes(data)
            result.append((round(start / sr, 3), round(end / sr, 3), segment_path))
            start = end
            index += 1
    return result


def transcribe_audio(
    original: Path,
    work_dir: Path,
    choice: str,
    progress: Callable[[str, int, str], None],
) -> dict:
    if choice not in MODELS:
        raise ValueError("Неизвестная модель")
    progress("preparing", 7, "Конвертация аудио в WAV")
    wav_path = work_dir / "converted.wav"
    convert_to_wav(original, wav_path)
    segments = split_wav(wav_path, work_dir)
    if not segments:
        raise ValueError("Не удалось найти звуковую дорожку")

    progress("loading", 15, "Загрузка установленной модели в память")
    model = load_model(choice)
    texts = []
    completed_segments = []
    duration = segments[-1][1]
    for index, (start, end, path) in enumerate(segments):
        progress("transcribing", 20 + round(75 * index / len(segments)),
                 f"Распознавание фрагмента {index + 1} из {len(segments)}")
        raw_text = model.transcribe(str(path))
        # Defensive handling for variants returning a transcription object.
        content = raw_text if isinstance(raw_text, str) else getattr(raw_text, "text", str(raw_text))
        content = content.strip()
        if content:
            texts.append(content)
            completed_segments.append({"start": start, "end": end, "text": content})
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    progress("finalizing", 97, "Сборка результата")
    return {
        "text": "\n\n".join(texts),
        "segments": completed_segments,
        "duration": round(duration, 2),
        "model": choice,
        "model_name": MODELS[choice],
    }
