"""Recursos a partir de una respuesta ya fundamentada en los PDF.

- Audio: local con Piper, una voz por idioma. No sale nada del equipo.
- Imagen: por defecto una infografía local (pregunta, respuesta y fuentes). Opcionalmente, con
  IMAGE_ENGINE=gemini, una ilustración de Gemini sin herramientas de búsqueda.
"""
import base64
import io
import re
import threading
import uuid
import wave
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone
from google import genai
from google.genai import types

from . import infographic, visuals
from .models import GeneratedAsset, Message

MAX_AUDIO_CHARS = 4000
MAX_IMAGE_CHARS = 900
Kind = GeneratedAsset.Kind
_voices: dict = {}
_voice_lock = threading.Lock()


class QuotaExceeded(Exception):
    pass


class GeminiUnavailable(Exception):
    pass


class VoiceUnavailable(Exception):
    pass


class ImageBillingRequired(Exception):
    """La cuenta de Gemini está en la capa gratuita, que no incluye imágenes."""


class GeminiBusy(Exception):
    pass


def _gemini_client():
    # Sin reintentos: la librería reintenta los errores 429 indefinidamente y la petición quedaría colgada.
    return genai.Client(
        api_key=settings.GEMINI_API_KEY,
        http_options=types.HttpOptions(timeout=90_000, retry_options=types.HttpRetryOptions(attempts=1)),
    )


def plain_text(markdown: str) -> str:
    text = re.sub(r"\[\d{1,2}\]", "", markdown)
    text = re.sub(r"[#*_`>]+", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _check_image_quota(user) -> None:
    used = GeneratedAsset.objects.filter(
        message__conversation__user=user, kind=Kind.IMAGE, created_at__date=timezone.localdate()
    ).count()
    if used >= settings.GEMINI_DAILY_IMAGE_LIMIT:
        raise QuotaExceeded


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


def _image_bytes(client, message: Message) -> tuple[bytes, str]:
    summary = plain_text(message.content)[:MAX_IMAGE_CHARS]
    prompt = (
        "Editorial conceptual illustration for an academic article, using a restrained palette of navy, orange and "
        "off-white. It must visually represent the ethical tension described below. Use symbols and abstract scenes. "
        "Do not include any text, logos, or recognizable real people.\n\nContent:\n" + summary
    )
    interaction = client.interactions.create(
        model=settings.GEMINI_IMAGE_MODEL,
        input=prompt,
        response_format={"type": "image", "mime_type": "image/jpeg", "aspect_ratio": "16:9", "image_size": "1K"},
    )
    return base64.b64decode(interaction.output_image.data), "jpg"


def generate(message: Message, kind: str) -> GeneratedAsset:
    """Reutiliza el recurso si ya existe, para no repetir trabajo ni gastar cuota de Gemini."""
    existing = message.assets.filter(kind=kind).first()
    if existing:
        return existing
    if kind in visuals.KINDS:
        data, extension, model = visuals.render_message(kind, message), "png", f"{kind}-local"
    elif kind == Kind.IMAGE and settings.IMAGE_ENGINE != "gemini":
        data, extension, model = infographic.render_message(message), "png", "infografia-local"
    elif kind == Kind.IMAGE:
        if not settings.GEMINI_API_KEY:
            raise GeminiUnavailable
        _check_image_quota(message.conversation.user)
        try:
            data, extension = _image_bytes(_gemini_client(), message)
        except Exception as exc:
            if getattr(exc, "status_code", None) == 429:
                raise (ImageBillingRequired if "free tier" in str(exc).lower() else GeminiBusy) from exc
            raise
        model = settings.GEMINI_IMAGE_MODEL
    else:
        data, extension, model = _audio_bytes(message)
    asset = GeneratedAsset(message=message, kind=kind, model_name=model)
    asset.file.save(f"{uuid.uuid4().hex}.{extension}", ContentFile(data), save=True)
    return asset
