"""Gemini solo da forma de imagen o audio a una respuesta ya fundamentada en los PDF.

No se le activa ninguna herramienta de búsqueda: nunca actúa como fuente de información.
"""
import base64
import re
import uuid

from django.conf import settings
from django.core.files.base import ContentFile
from django.utils import timezone
from google import genai

from .models import GeneratedAsset, Message

MAX_AUDIO_CHARS = 1800
MAX_IMAGE_CHARS = 900
Kind = GeneratedAsset.Kind


class QuotaExceeded(Exception):
    pass


class GeminiUnavailable(Exception):
    pass


def plain_text(markdown: str) -> str:
    text = re.sub(r"\[\d{1,2}\]", "", markdown)
    text = re.sub(r"[#*_`>]+", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def _daily_limit(kind: str) -> int:
    return settings.GEMINI_DAILY_IMAGE_LIMIT if kind == Kind.IMAGE else settings.GEMINI_DAILY_AUDIO_LIMIT


def _check_quota(user, kind: str) -> None:
    used = GeneratedAsset.objects.filter(
        message__conversation__user=user, kind=kind, created_at__date=timezone.localdate()
    ).count()
    if used >= _daily_limit(kind):
        raise QuotaExceeded


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
        response_format={"type": "image", "mime_type": "image/png", "aspect_ratio": "16:9", "image_size": "1K"},
    )
    return base64.b64decode(interaction.output_image.data), "png"


def _audio_bytes(client, message: Message) -> tuple[bytes, str]:
    interaction = client.interactions.create(
        model=settings.GEMINI_TTS_MODEL,
        input=plain_text(message.content)[:MAX_AUDIO_CHARS],
        response_format={"type": "audio"},
        generation_config={"speech_config": [{"voice": "Kore"}]},
    )
    return base64.b64decode(interaction.output_audio.data), "wav"


def generate(message: Message, kind: str) -> GeneratedAsset:
    """Reutiliza el recurso si ya existe, para no volver a consumir la cuota de Gemini."""
    existing = message.assets.filter(kind=kind).first()
    if existing:
        return existing
    if not settings.GEMINI_API_KEY:
        raise GeminiUnavailable
    _check_quota(message.conversation.user, kind)
    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    data, extension = _image_bytes(client, message) if kind == Kind.IMAGE else _audio_bytes(client, message)
    model = settings.GEMINI_IMAGE_MODEL if kind == Kind.IMAGE else settings.GEMINI_TTS_MODEL
    asset = GeneratedAsset(message=message, kind=kind, model_name=model)
    asset.file.save(f"{uuid.uuid4().hex}.{extension}", ContentFile(data), save=True)
    return asset
