"""Local GigaAM inference and bounded, silence-aware WAV segmentation."""

from __future__ import annotations

import gc
import shutil
import threading
import wave
from types import MethodType
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
_current_device: str | None = None
_current_model = None


def _prepare_normalized_wav(self, wav_file: str):
    """Feed already-converted 16-kHz mono PCM directly into GigaAM.

    GigaAM's stock prepare_wav/load_audio calls an external executable named
    `ffmpeg` even for normalized WAV files. On Windows our bundled imageio-ffmpeg
    has a versioned filename and is deliberately not installed on PATH.
    Reading the PCM frames in-process avoids that redundant, fragile subprocess.
    """
    import torch

    with wave.open(str(wav_file), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, SAMPLE_RATE):
            raise ValueError("Неверный формат сегмента: ожидается 16 kHz, mono, PCM16")
        raw = wav.readframes(wav.getnframes())

    # Copy before torch.from_numpy to give PyTorch a writable, owned buffer.
    # Values and scaling match GigaAM's own load_audio() implementation.
    pcm = np.frombuffer(raw, dtype="<i2").copy()
    audio = torch.from_numpy(pcm).float().div_(32768.0)
    audio = audio.to(self._device).to(self._dtype).unsqueeze(0)
    lengths = torch.full((1,), audio.shape[-1], device=self._device, dtype=torch.long)
    return audio, lengths


def _use_normalized_wav_loader(model) -> None:
    """Override only per-instance audio preparation, not GigaAM inference."""
    model.prepare_wav = MethodType(_prepare_normalized_wav, model)


def device_info() -> dict:
    """Do not silently claim GPU support in a CPU-only torch distribution."""
    try:
        import torch
        cuda = bool(torch.cuda.is_available())
        return {
            "cuda_available": cuda,
            "gpu_name": torch.cuda.get_device_name(0) if cuda else None,
            "torch_version": torch.__version__,
            "cuda_runtime": torch.version.cuda,
            "reason": None if cuda else (
                "Установлена CPU-сборка PyTorch" if torch.version.cuda is None else
                "CUDA недоступна: проверьте драйвер NVIDIA и совместимость видеокарты"
            ),
        }
    except Exception as exc:
        return {"cuda_available": False, "gpu_name": None, "torch_version": None,
                "cuda_runtime": None, "reason": str(exc)}


def resolve_device(preference: str) -> str:
    if preference not in ("auto", "cpu", "cuda"):
        raise ValueError("Неизвестное устройство вычислений")
    if preference == "cpu":
        return "cpu"
    info = device_info()
    if preference == "cuda" and not info["cuda_available"]:
        raise RuntimeError("GPU NVIDIA недоступна: " + (info["reason"] or "CUDA не обнаружена"))
    return "cuda" if info["cuda_available"] else "cpu"


def load_model(choice: str, device: str = "auto"):

    """Keep only one model in memory, even when users switch variants."""
    global _current_model, _current_model_name, _current_device
    if choice not in MODELS:
        raise ValueError("Неизвестная модель")

    target_device = resolve_device(device)
    with _model_lock:
        name = MODELS[choice]
        if _current_model_name == name and _current_device == target_device and _current_model is not None:
            return _current_model
        _current_model = None
        _current_model_name = None
        _current_device = None
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
        model = gigaam.load_model(name, download_root=str(folder(choice)), device=target_device)
        _use_normalized_wav_loader(model)
        _current_model = model
        _current_model_name = name
        _current_device = target_device
        return model


def unload_model(choice: str) -> None:
    """Release the selected model before its files are removed from disk."""
    global _current_model, _current_model_name, _current_device
    with _model_lock:
        if _current_model_name == MODELS[choice]:
            _current_model = None
            _current_model_name = None
            _current_device = None
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


def _normalized_audio_tensor(path: str):
    """Avoid calling GigaAM's external ffmpeg from its VAD adapter.

    The input has already been converted to mono 16k PCM by GigaScribe.
    """
    import torch
    with wave.open(str(path), "rb") as w:
        if (w.getnchannels(), w.getsampwidth(), w.getframerate()) != (1, 2, SAMPLE_RATE):
            raise ValueError("VAD ожидает PCM16 16 kHz mono")
        samples = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").copy()
    return torch.from_numpy(samples).float() / 32768.0


def _prepare_vad_adapter() -> None:
    """Let official transcribe_longform segment/batch/decode without external FFmpeg.

    Pyannote accepts a pre-decoded {'waveform', 'sample_rate'} dictionary, avoiding
    TorchCodec's filesystem decoding dependency. The ASR code stays upstream.
    """
    import gigaam.vad_utils as vad_utils
    from backend import vad_store
    if not vad_store.installed():
        raise RuntimeError("Для умной сегментации скачайте VAD в разделе Модели")
    vad_utils.resolve_local_segmentation_path = lambda model_id: str(vad_store.weights_dir())
    vad_utils.load_audio = _normalized_audio_tensor
    if getattr(vad_utils, "_gigascribe_wrapped", False):
        return
    original_get_pipeline = vad_utils.get_pipeline

    class DecodedPipeline:
        def __init__(self, original):
            self.original = original

        def __call__(self, wav_file):
            waveform = _normalized_audio_tensor(wav_file).unsqueeze(0)
            return self.original({"waveform": waveform, "sample_rate": SAMPLE_RATE})

    def get_pipeline(device, model_id="pyannote/segmentation-3.0"):
        return DecodedPipeline(original_get_pipeline(device, model_id))

    vad_utils.get_pipeline = get_pipeline
    vad_utils._gigascribe_wrapped = True


def transcribe_audio(
    original: Path,
    work_dir: Path,
    choice: str,
    progress: Callable[[str, int, str], None],
    mode: str = "standard",
    device: str = "auto",
) -> dict:
    if choice not in MODELS:
        raise ValueError("Неизвестная модель")
    if mode not in ("standard", "vad"):
        raise ValueError("Неизвестный режим сегментации")
    target_device = resolve_device(device)
    progress("preparing", 7, "Конвертация аудио в WAV")
    wav_path = work_dir / "converted.wav"
    convert_to_wav(original, wav_path)
    with wave.open(str(wav_path), "rb") as audio:
        duration = audio.getnframes() / audio.getframerate()
    if duration <= 0 or duration > 7200:
        raise ValueError("Аудиозапись должна быть не длиннее двух часов")
    progress("loading", 15, f"Загрузка модели на {target_device.upper()}")
    model = load_model(choice) if device == "auto" else load_model(choice, device)
    completed_segments = []
    if mode == "vad":
        progress("transcribing", 26, "VAD: поиск речи и пакетное распознавание")
        _prepare_vad_adapter()
        # Conservative batch size avoids GPU out-of-memory on smaller VRAM cards.
        result = model.transcribe_longform(str(wav_path), fr_batch_size=2, fr_num_workers=0)
        for seg in result.segments:
            content = str(seg.text).strip()
            if content:
                completed_segments.append({"start": round(float(seg.start), 3),
                                           "end": round(float(seg.end), 3), "text": content})
    else:
        segments = split_wav(wav_path, work_dir)
        for index, (start, end, path) in enumerate(segments):
            progress("transcribing", 20 + round(75 * index / len(segments)),
                     f"Распознавание фрагмента {index + 1} из {len(segments)}")
            raw_text = model.transcribe(str(path))
            content = raw_text if isinstance(raw_text, str) else getattr(raw_text, "text", str(raw_text))
            content = content.strip()
            if content:
                completed_segments.append({"start": start, "end": end, "text": content})
            path.unlink(missing_ok=True)
    progress("finalizing", 97, "Сборка результата")
    return {
        "text": "\n\n".join(s["text"] for s in completed_segments),
        "segments": completed_segments,
        "duration": round(duration, 2), "model": choice, "model_name": MODELS[choice],
        "mode": mode, "device": target_device,
    }
