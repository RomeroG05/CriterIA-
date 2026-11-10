"""Detección automática de idioma limitada a español, inglés y portugués."""
from lingua import Language, LanguageDetectorBuilder

_CODES = {Language.SPANISH: "es", Language.ENGLISH: "en", Language.PORTUGUESE: "pt"}
_detector = LanguageDetectorBuilder.from_languages(*_CODES).with_minimum_relative_distance(0.05).build()
LANGUAGE_LABEL = {"es": "Español", "en": "English", "pt": "Português"}


def detect(text: str, fallback: str = "es") -> str:
    """Devuelve el idioma; en textos muy cortos o dudosos mantiene el de la conversación."""
    if len(text.split()) < 2 and len(text) < 12:
        return fallback
    return _CODES.get(_detector.detect_language_of(text), fallback)
