import json
import logging
import tempfile
import time
from collections import OrderedDict
from datetime import timedelta

import ollama
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.http import FileResponse, HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import get_language
from django.utils.translation import gettext as _
from django.views.decorators.http import require_GET, require_POST

from corpus.models import Chunk, Document

from . import export, media, pipeline, speech, texts
from .models import Citation, Conversation, GeneratedAsset, Message, Mode

log = logging.getLogger(__name__)
MAX_MESSAGE = 2000
MAX_AUDIO_BYTES = 15 * 1024 * 1024
S = Message.Status


def _allowed(request, scope: str, limit: int, window: int = 600) -> bool:
    who = request.user.pk if request.user.is_authenticated else request.META.get("REMOTE_ADDR", "?")
    key = f"rl:{scope}:{who}"
    cache.add(key, 0, window)
    try:
        return cache.incr(key) <= limit
    except ValueError:  # la clave expiró entre add e incr
        return True


def _ui_language() -> str:
    code = (get_language() or "es")[:2]
    return code if code in {"es", "en", "pt"} else "es"


def js_strings() -> dict:
    return {
        "modeA": _("Conceptos"),
        "modeB": _("Casos"),
        "modeC": _("Dilemas"),
        "placeholderA": _("Pregunta por un concepto, principio o término…"),
        "placeholderB": _("Escribe una temática o busca un caso…"),
        "placeholderC": _("Describe la situación o plantea el dilema…"),
        "analyzing": _("Analizando tu consulta…"),
        "searching": _("Buscando en los documentos…"),
        "writing": _("Redactando la respuesta…"),
        "translating": _("Traduciendo la respuesta…"),
        "sources": _("Fuentes"),
        "page": _("p."),
        "similarity": _("similitud"),
        "copy": _("Copiar"),
        "copied": _("Copiado"),
        "useful": _("Útil"),
        "notUseful": _("No útil"),
        "audio": _("Generar audio"),
        "image": _("Generar imagen"),
        "generating": _("Generando…"),
        "registerToGenerate": _("Regístrate para generar imagen y audio"),
        "geminiMissing": _("La generación con Gemini no está configurada"),
        "quota": _("Alcanzaste el límite diario para este recurso"),
        "genError": _("No se pudo generar el recurso. Inténtalo nuevamente."),
        "retry": _("Reintentar"),
        "stopped": _("Respuesta detenida."),
        "languageDetected": _("Idioma detectado"),
        "recording": _("Grabando… pulsa para terminar"),
        "transcribing": _("Transcribiendo…"),
        "micDenied": _("No se pudo acceder al micrófono"),
        "voiceError": _("No se pudo transcribir el audio"),
        "aiOnline": _("IA local activa"),
        "aiOffline": _("IA local sin conexión"),
        "documents": _("documentos"),
        "confirmDelete": _("¿Eliminar esta conversación?"),
        "rename": _("Nuevo nombre de la conversación"),
        "today": _("Hoy"),
        "cite": _("Citar"),
        "incompleteCitation": _("Faltan datos de autor o año en este documento: completa la referencia antes de usarla."),
        "inText": _("Cita en el texto"),
        "reference": _("Referencia"),
        "conceptPrinciple": _("Explícame el principio de %(name)s en el periodismo con IA"),
        "casePrinciple": _("Busca casos relacionados con %(name)s en comunicación y periodismo"),
        "dilemmaPrinciple": _("Plantea los dilemas éticos relacionados con %(name)s en una redacción que usa IA"),
    }


def _serialize_message(message: Message, latest: bool = False) -> dict:
    data = {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "status": message.status,
        "mode": message.mode,
        "language": message.language,
        "feedback": message.feedback,
        "citations": [
            {"number": c.number, "chunk_id": c.chunk_id, "title": c.title, "page": c.page,
             "similarity": c.similarity, "quote": c.quote}
            for c in message.citations.all()
        ],
        "assets": [{"kind": a.kind, "url": reverse("chat:asset", args=[a.id])} for a in message.assets.all()],
    }
    if latest and message.role == "assistant" and message.status == S.ANSWER:
        data["suggestions"] = texts.suggestions(message.mode, message.language)
    return data


def _history_groups(user) -> list[dict]:
    today = timezone.localdate()
    labels = OrderedDict([("today", _("Hoy")), ("yesterday", _("Ayer")), ("week", _("Últimos 7 días")), ("older", _("Anteriores"))])
    groups: dict[str, list] = {key: [] for key in labels}
    for conversation in Conversation.objects.filter(user=user)[:200]:
        day = timezone.localdate(conversation.updated_at)
        key = "today" if day == today else "yesterday" if day == today - timedelta(days=1) else "week" if day > today - timedelta(days=7) else "older"
        groups[key].append(conversation)
    return [{"label": labels[key], "items": items} for key, items in groups.items() if items]


def _render_app(request, conversation=None):
    messages = []
    if conversation:
        rows = list(conversation.messages.prefetch_related("citations", "assets"))
        messages = [_serialize_message(m, latest=i == len(rows) - 1) for i, m in enumerate(rows)]
    bootstrap = {
        "authenticated": request.user.is_authenticated,
        "conversationId": str(conversation.id) if conversation else None,
        "title": conversation.title if conversation else "",
        "mode": conversation.mode if conversation else Mode.CONCEPTS,
        "messages": messages,
        "uiLanguage": _ui_language(),
        "gemini": bool(settings.GEMINI_API_KEY),
        "i18n": js_strings(),
    }
    principles = [_("Veracidad"), _("Transparencia"), _("Autoría"), _("Privacidad"), _("Independencia"), _("Pluralismo"), _("Responsabilidad")]
    groups = _history_groups(request.user) if request.user.is_authenticated else []
    return render(request, "chat/app.html", {"bootstrap": bootstrap, "groups": groups, "principles": principles, "conversation": conversation})


@require_GET
def home(request):
    return _render_app(request)


@login_required
@require_GET
def conversation_detail(request, pk):
    return _render_app(request, get_object_or_404(Conversation, pk=pk, user=request.user))


@require_GET
def guide(request):
    return render(request, f"pages/guide_{_ui_language()}.html")


@require_GET
def api_status(request):
    online, model_ready = False, False
    try:
        names = [m["model"] for m in ollama.Client(host=settings.OLLAMA_HOST, timeout=3).list()["models"]]
        online = True
        model_ready = settings.OLLAMA_CHAT_MODEL in names
    except Exception:  # cualquier fallo de conexión equivale a "sin conexión"
        pass
    return JsonResponse(
        {
            "online": online and model_ready,
            "documents": Document.objects.filter(status=Document.Status.READY).count(),
            "chunks": Chunk.objects.count(),
        }
    )


def _parse_guest_history(raw) -> list[pipeline.Turn]:
    """El invitado envía su contexto reciente: en el servidor no se guarda nada suyo."""
    turns = []
    if isinstance(raw, list):
        for item in raw[-4:]:
            if isinstance(item, dict) and item.get("role") in {"user", "assistant"} and isinstance(item.get("content"), str):
                turns.append(pipeline.Turn(item["role"], item["content"][:1500]))
    return turns


def _user_history(conversation: Conversation) -> list[pipeline.Turn]:
    rows = list(conversation.messages.order_by("-created_at", "-id")[:8])[::-1]
    turns, pending = [], None
    for row in rows:  # solo pares con respuesta fundamentada aportan contexto útil
        if row.role == "user":
            pending = row
        elif pending is not None and row.status == S.ANSWER:
            turns += [pipeline.Turn("user", pending.content), pipeline.Turn("assistant", row.content)]
            pending = None
    return turns


def _save_answer(conversation, final, latency_ms) -> Message:
    message = Message.objects.create(
        conversation=conversation,
        role=Message.Role.ASSISTANT,
        content=final["content"],
        status=final["status"],
        mode=final["mode"],
        language=final["language"],
        model_name=final.get("model", ""),
        latency_ms=latency_ms,
    )
    existing = set(Chunk.objects.filter(id__in=[c["chunk_id"] for c in final["citations"]]).values_list("id", flat=True))
    Citation.objects.bulk_create(
        [
            Citation(message=message, chunk_id=c["chunk_id"] if c["chunk_id"] in existing else None, number=c["number"],
                     title=c["title"], page=c["page"], similarity=c["similarity"], quote=c["quote"])
            for c in final["citations"]
        ]
    )
    conversation.language = final["language"]
    conversation.mode = final["mode"]
    conversation.save(update_fields=["language", "mode", "updated_at"])
    return message


def _line(event: dict) -> bytes:
    return (json.dumps(event, ensure_ascii=False) + "\n").encode()


@require_POST
def api_chat(request):
    if not _allowed(request, "chat", 120 if request.user.is_authenticated else 30):
        return JsonResponse({"error": _("Demasiadas consultas. Espera unos minutos.")}, status=429)
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "JSON inválido"}, status=400)
    text = str(payload.get("message", "")).strip()
    mode = payload.get("mode") if payload.get("mode") in Mode.values else Mode.CONCEPTS
    if not text or len(text) > MAX_MESSAGE:
        return JsonResponse({"error": _("El mensaje debe tener entre 1 y 2000 caracteres.")}, status=400)

    conversation, history = None, []
    fallback = _ui_language()
    if request.user.is_authenticated:
        if payload.get("conversation_id"):
            conversation = get_object_or_404(Conversation, pk=payload["conversation_id"], user=request.user)
            history = _user_history(conversation)
            fallback = conversation.language
        else:
            conversation = Conversation.objects.create(user=request.user, title=text[:80], mode=mode, language=fallback)
        user_message = Message.objects.create(conversation=conversation, role=Message.Role.USER, content=text, mode=mode, language=fallback)
    else:
        user_message = None
        history = _parse_guest_history(payload.get("history"))
        if isinstance(payload.get("language"), str) and payload["language"] in {"es", "en", "pt"}:
            fallback = payload["language"]

    is_new = bool(conversation) and not payload.get("conversation_id")

    def stream():
        started = time.monotonic()
        partial, language, finished = [], fallback, False
        events = pipeline.run(text, mode, history, fallback)
        if conversation:
            yield _line({"type": "conversation", "id": str(conversation.id), "title": conversation.title, "new": is_new,
                         "url": reverse("chat:conversation", args=[conversation.id])})
        try:
            for event in events:
                kind = event["type"]
                if kind == "language":
                    language = event["language"]
                    if user_message:
                        Message.objects.filter(pk=user_message.pk).update(language=language)
                elif kind == "token":
                    partial.append(event["text"])
                elif kind == "error":
                    finished = True
                    if conversation:  # un error no deja rastro: el usuario puede reintentar limpiamente
                        user_message.delete()
                        if not conversation.messages.exists():
                            conversation.delete()
                            event["conversation_removed"] = True
                elif kind == "final":
                    finished = True
                    if conversation:
                        event["message_id"] = _save_answer(conversation, event, int((time.monotonic() - started) * 1000)).id
                yield _line(event)
        finally:
            events.close()
            if conversation and not finished:  # el usuario pulsó «detener»
                content = "".join(partial).strip()
                if content:
                    Message.objects.create(conversation=conversation, role=Message.Role.ASSISTANT, content=content,
                                           status=S.STOPPED, mode=mode, language=language)
                else:
                    user_message.delete()

    response = StreamingHttpResponse(stream(), content_type="application/x-ndjson; charset=utf-8")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@require_POST
def api_transcribe(request):
    if not _allowed(request, "voice", 30):
        return JsonResponse({"error": "rate"}, status=429)
    upload = request.FILES.get("audio")
    if upload is None or upload.size > MAX_AUDIO_BYTES:
        return JsonResponse({"error": "audio"}, status=400)
    with tempfile.NamedTemporaryFile(suffix=".webm") as handle:
        for block in upload.chunks():
            handle.write(block)
        handle.flush()
        try:
            text, language = speech.transcribe(handle.name)
        except Exception:
            log.exception("Fallo de transcripción")
            return JsonResponse({"error": "transcribe"}, status=500)
    return JsonResponse({"text": text, "language": language})


def _owned_message(request, pk) -> Message:
    return get_object_or_404(Message, pk=pk, role=Message.Role.ASSISTANT, conversation__user=request.user)


@login_required
@require_POST
def api_feedback(request, pk):
    message = _owned_message(request, pk)
    try:
        value = int(json.loads(request.body).get("value", 0))
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({"error": "valor"}, status=400)
    message.feedback = max(-1, min(1, value))
    message.save(update_fields=["feedback"])
    return JsonResponse({"feedback": message.feedback})


@login_required
@require_POST
def api_generate(request, pk, kind):
    if kind not in GeneratedAsset.Kind.values:
        return JsonResponse({"error": "tipo"}, status=400)
    message = _owned_message(request, pk)
    if message.status != S.ANSWER:
        return JsonResponse({"error": "not_answer"}, status=400)
    try:
        asset = media.generate(message, kind)
    except media.GeminiUnavailable:
        return JsonResponse({"error": "gemini_missing"}, status=503)
    except media.QuotaExceeded:
        return JsonResponse({"error": "quota"}, status=429)
    except Exception:
        log.exception("Fallo al generar %s", kind)
        return JsonResponse({"error": "generation"}, status=502)
    return JsonResponse({"kind": asset.kind, "url": reverse("chat:asset", args=[asset.id])})


@login_required
@require_GET
def asset_file(request, pk):
    asset = get_object_or_404(GeneratedAsset, pk=pk, message__conversation__user=request.user)
    content_type = "image/png" if asset.kind == GeneratedAsset.Kind.IMAGE else "audio/wav"
    return FileResponse(asset.file.open("rb"), content_type=content_type)


@login_required
@require_POST
def conversation_rename(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk, user=request.user)
    try:
        title = str(json.loads(request.body).get("title", "")).strip()[:120]
    except json.JSONDecodeError:
        title = ""
    if not title:
        return JsonResponse({"error": "title"}, status=400)
    Conversation.objects.filter(pk=pk).update(title=title)
    return JsonResponse({"title": title})


@login_required
@require_POST
def conversation_delete(request, pk):
    get_object_or_404(Conversation, pk=pk, user=request.user).delete()
    return JsonResponse({"deleted": True})


@login_required
@require_GET
def conversation_export(request, pk):
    conversation = get_object_or_404(Conversation, pk=pk, user=request.user)
    pdf = export.conversation_pdf(conversation, {"you": request.user.display_name})
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="criteria-{str(conversation.id)[:8]}.pdf"'
    return response
