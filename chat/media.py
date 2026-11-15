"""Recursos generados en el propio equipo a partir de una respuesta ya fundamentada en los PDF.

- Audio: lectura en voz alta con Piper, una voz por idioma.
- Visuales: infografía, mapa conceptual, diagrama de Ishikawa y cuadro comparativo.
No se usa internet ni ningún servicio externo.
"""
import io
import re
import threading
import uuid
import wave
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile

from . import infographic, visuals
from .models import GeneratedAsset, Message

MAX_AUDIO_CHARS = 4000
Kind = GeneratedAsset.Kind
_voices: dict = {}
_voice_lock = threading.Lock()


class VoiceUnavailable(Exception):
    pass


def plain_text(markdown: str) -> str:
    text = re.sub(r"\[\d{1,2}\]", "", markdown)
    text = re.sub(r"[#*_`>]+", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _voice(language: str):
    """Carga la voz del idioma una sola vez por proceso."""
    name = settings.PIPER_VOICES.get(language) or settings.PIPER_VOICES["es"]
    with _voice_lock:
        if name not in _voices:
            path = Path(settings.PIPER_VOICES_DIR) / f"{name}.onnx"
            if not path.exists():
                raise VoiceUnavailable(name)
            from piper import PiperVoice

            _voices[name] = PiperVoice.load(path)
        return name, _voices[name]


def _audio_bytes(message: Message) -> tuple[bytes, str, str]:
    name, voice = _voice(message.language)
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        voice.synthesize_wav(plain_text(message.content)[:MAX_AUDIO_CHARS], wav_file)
    return buffer.getvalue(), "wav", f"piper:{name}"


def generate(message: Message, kind: str) -> GeneratedAsset:
    """Reutiliza el recurso si ya existe, para no repetir trabajo."""
    existing = message.assets.filter(kind=kind).first()
    if existing:
        return existing
    if kind == Kind.AUDIO:
        data, extension, model = _audio_bytes(message)
    elif kind == Kind.IMAGE:
        data, extension, model = infographic.render_message(message), "png", "infografia-local"
    else:
        data, extension, model = visuals.render_message(kind, message), "png", f"{kind}-local"
    asset = GeneratedAsset(message=message, kind=kind, model_name=model)
    asset.file.save(f"{uuid.uuid4().hex}.{extension}", ContentFile(data), save=True)
    return asset
