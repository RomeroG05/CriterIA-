"""Transcripción de voz local con faster-whisper. El modelo se carga al usarlo por primera vez."""
import threading

from django.conf import settings

ALLOWED = {"es", "en", "pt"}
_lock = threading.Lock()
_model = None


def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel

            _model = WhisperModel(settings.WHISPER_MODEL, device="cpu", compute_type="int8")
        return _model


def transcribe(path: str) -> tuple[str, str]:
    model = _get_model()
    segments, info = model.transcribe(path, vad_filter=True, beam_size=3)
    language = info.language
    if language not in ALLOWED:  # p. ej. gallego o catalán: se fuerza el idioma soportado por defecto
        language = "es"
        segments, info = model.transcribe(path, vad_filter=True, beam_size=3, language=language)
    return " ".join(segment.text.strip() for segment in segments).strip(), language
