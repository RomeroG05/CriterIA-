"""Detección automática de idioma limitada a español, inglés y portugués."""
import re

from lingua import Language, LanguageDetectorBuilder

_CODES = {Language.SPANISH: "es", Language.ENGLISH: "en", Language.PORTUGUESE: "pt"}
_detector = LanguageDetectorBuilder.from_languages(*_CODES).with_minimum_relative_distance(0.05).build()
LANGUAGE_LABEL = {"es": "Español", "en": "English", "pt": "Português"}

# En frases cortas el detector confunde español y portugués; estas marcas solo existen en uno de ellos.
# «nh» y «lh» no cuentan al inicio de palabra («inhumano», «anhelo», «alhaja» son españolas).
_PT_MARKS = re.compile(r"ção\b|ções\b|ão\b|ões\b|[ãõêô]|(?<!\b[aei])nh|(?<!\ba)lh|\b(é|não|você|vocês|um|uma|em|ao|aos|à|às|com|isso|isto|também|pelo|pela)\b", re.I)
_ES_MARKS = re.compile(r"[¿¡ñ]|ción\b|ciones\b|\b(el|los|del|una|es|y|con|qué|cómo|cuál|cuáles|también|usted|pero|hay|muy)\b", re.I)


def _settle_spanish_portuguese(text: str, guess: str) -> str:
    portuguese, spanish = len(_PT_MARKS.findall(text)), len(_ES_MARKS.findall(text))
    if guess == "es" and portuguese > spanish:
        return "pt"
    if guess == "pt" and spanish > portuguese:
        return "es"
    return guess


def dominant(text: str) -> str:
    """Idioma con mayor confianza, aunque la diferencia sea pequeña (útil en textos mezclados)."""
    return _CODES[_detector.compute_language_confidence_values(text)[0].language]


def detect(text: str, fallback: str = "es") -> str:
    """Devuelve el idioma; en textos muy cortos o dudosos mantiene el de la conversación."""
    if len(text.split()) < 2 and len(text) < 12:
        return fallback
    guess = _CODES.get(_detector.detect_language_of(text), fallback)
    return _settle_spanish_portuguese(text, guess) if guess in ("es", "pt") else guess
