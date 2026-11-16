"""Speech-to-text with faster-whisper (local, word-level timestamps)."""

import logging
import os
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from app.core.config import get_settings
from app.core.exceptions import ExternalServiceError
from app.services.sentences import Word

# Windows without Developer Mode can't create symlinks; the HF cache still works.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

logger = logging.getLogger(__name__)
_model_lock = threading.Lock()


class TranscriptionError(ExternalServiceError):
    code = "transcription_error"


@dataclass(frozen=True)
class TranscriptionResult:
    language: str
    language_probability: float
    duration_seconds: float
    model: str
    words: list[Word]


@lru_cache(maxsize=2)
def _load_model(name: str, device: str, compute_type: str, cpu_threads: int):
    from faster_whisper import WhisperModel  # heavy import, only in the worker

    logger.info("Loading Whisper model '%s' (%s, %s)", name, device, compute_type)
    return WhisperModel(name, device=device, compute_type=compute_type, cpu_threads=cpu_threads)


def get_model():
    """Load (and on first use download) the configured model; cached per process."""
    s = get_settings()
    with _model_lock:
        try:
            return _load_model(s.whisper_model, s.whisper_device, s.whisper_compute_type,
                               s.whisper_cpu_threads)
        except Exception as exc:
            raise TranscriptionError(f"Could not load Whisper model '{s.whisper_model}': {exc}") \
                from exc


SAMPLE_RATE = 16000


def load_wav(path: Path) -> np.ndarray:
    """Read a 16 kHz mono 16-bit PCM WAV (as written by media.extract_audio) as float32.

    Passing samples directly avoids faster-whisper's PyAV decoder, whose arguments break
    between PyAV releases.
    """
    try:
        with wave.open(str(path), "rb") as wav:
            if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (SAMPLE_RATE, 1, 2):
                raise TranscriptionError(
                    f"Expected 16 kHz mono 16-bit WAV, got {wav.getframerate()} Hz, "
                    f"{wav.getnchannels()} channel(s), {8 * wav.getsampwidth()}-bit."
                )
            frames = wav.readframes(wav.getnframes())
    except (OSError, wave.Error, EOFError) as exc:
        raise TranscriptionError(f"Could not read audio file {path.name}: {exc}") from exc
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0


def transcribe(
    audio_path: Path,
    *,
    duration_seconds: float | None = None,
    on_progress: Callable[[float], None] | None = None,
) -> TranscriptionResult:
    """Transcribe a 16 kHz WAV file. Progress is reported as each segment finishes.

    Voice-activity detection (VAD) skips silence and music, which is faster and prevents
    hallucinated text. It can also discard speech over loud music (music videos, vlogs
    with a soundtrack); if it leaves no words at all, the audio is transcribed again
    without it.
    """
    settings = get_settings()
    model = get_model()
    audio = load_wav(audio_path)
    started = time.monotonic()
    words: list[Word] = []
    info = None
    for use_vad in (True, False):
        try:
            segments, info = model.transcribe(
                audio,
                language=settings.whisper_language,
                beam_size=settings.whisper_beam_size,
                word_timestamps=True,
                vad_filter=use_vad,
                condition_on_previous_text=False,  # avoids repetition loops on long audio
            )
            total = duration_seconds or info.duration or 0
            for segment in segments:  # generator: decoding happens while iterating
                for w in segment.words or []:
                    if w.word.strip():
                        words.append(Word(start=round(w.start, 3), end=round(w.end, 3),
                                          text=w.word, probability=round(w.probability, 3)))
                if on_progress and total:
                    on_progress(min(0.99, segment.end / total))
        except Exception as exc:
            logger.exception("Transcription failed for %s", audio_path)
            raise TranscriptionError(f"Transcription failed: {exc}") from exc
        if words:
            break
        if use_vad:
            logger.warning("VAD found no speech in %s; retrying without VAD", audio_path.name)

    assert info is not None
    elapsed = time.monotonic() - started
    logger.info("Transcribed %.0fs of audio in %.0fs (%.2fx real time), %d words, lang=%s",
                info.duration, elapsed, info.duration / max(elapsed, 0.01), len(words),
                info.language)
    if on_progress and words:
        on_progress(1.0)
    return TranscriptionResult(
        language=info.language,
        language_probability=round(info.language_probability, 3),
        duration_seconds=round(info.duration, 3),
        model=settings.whisper_model,
        words=words,
    )
