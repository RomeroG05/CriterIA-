"""Transcripción de voz local con faster-whisper. El modelo se carga al usarlo por primera vez."""
import logging
import threading

import numpy as np
from django.conf import settings

log = logging.getLogger(__name__)
ALLOWED = {"es", "en", "pt"}
RATE = 16_000
MIN_SECONDS = 0.6
SILENCE_PEAK = 0.01  # por debajo, el micrófono no captó sonido (mudo o dispositivo equivocado)
_lock = threading.Lock()
_model = None


class TooShort(Exception):
    pass


class SilentAudio(Exception):
    pass


class NoSpeech(Exception):
    pass


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(settings.WHISPER_MODEL, device="cpu", compute_type="int8")
        return _model


def _run(model, audio, vad: bool) -> tuple[str, str]:
    segments, info = model.transcribe(audio, vad_filter=vad, beam_size=3)
    language = info.language
    if language not in ALLOWED:  # p. ej. gallego o catalán: se fuerza el idioma soportado por defecto
        language = "es"
        segments, info = model.transcribe(audio, vad_filter=vad, beam_size=3, language=language)
    return " ".join(segment.text.strip() for segment in segments).strip(), language


def transcribe(path: str) -> tuple[str, str]:
    from faster_whisper.audio import decode_audio

    audio = decode_audio(path, sampling_rate=RATE)
    seconds = len(audio) / RATE
    peak = float(np.abs(audio).max()) if len(audio) else 0.0
    log.info("Audio recibido: %.1f s, pico %.3f", seconds, peak)
    if seconds < MIN_SECONDS:
        raise TooShort
    if peak < SILENCE_PEAK:
        raise SilentAudio
    if peak < 0.3:  # voz muy baja: se amplifica antes de transcribir
        audio = audio * (0.5 / peak)
    model = _get_model()
    text, language = _run(model, audio, vad=True)
    if not text:  # el detector de voz puede descartar una voz débil; se intenta sin él
        text, language = _run(model, audio, vad=False)
    if not text:
        raise NoSpeech
    return text, language
