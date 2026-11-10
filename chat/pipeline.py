"""Flujo de CriterIA: idioma -> intención -> búsqueda en PDF -> ¿evidencia suficiente? -> respuesta o limitación."""
import json
import logging
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass

import ollama
from django.conf import settings

from corpus import retrieval

from . import language as lang_module
from . import prompts, texts
from .models import Message

log = logging.getLogger(__name__)

CITATION_RE = re.compile(r"\[(\d{1,2})\]")
_GREETING = re.compile(r"^(hola|holi|buenas|buenos dias|buenas tardes|buenas noches|saludos|hello|hi|hey|good (morning|afternoon|evening)|ola|oi|bom dia|boa tarde|boa noite)\b[\s!.,]*(.{0,20})$")
_FAREWELL = re.compile(r"^(adios|chao|chau|hasta (luego|pronto)|nos vemos|bye|goodbye|see you|tchau|ate (logo|breve|mais)|eso es todo|that'?s all|e isso|gracias,? (adios|hasta)|thanks,? bye|obrigad[oa],? tchau)\b")
_WORD_LANGUAGE = (
    ("pt", re.compile(r"^(ola|oi|bom dia|boa tarde|boa noite|tchau|ate (logo|breve|mais)|obrigad[oa])\b")),
    ("es", re.compile(r"^(hola|holi|buenas|buenos dias|saludos|adios|chao|chau|hasta|nos vemos|gracias)\b")),
    ("en", re.compile(r"^(hello|hi|hey|good (morning|afternoon|evening)|bye|goodbye|see you|thanks)\b")),
)
S = Message.Status


@dataclass
class Turn:
    role: str
    content: str


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower().strip())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lstrip("¡¿ ")


def _client() -> ollama.Client:
    return ollama.Client(host=settings.OLLAMA_HOST, timeout=settings.OLLAMA_TIMEOUT)


def clean_history(history: list[Turn]) -> list[Turn]:
    """Últimos turnos sin marcas [n], que no corresponden a las fuentes de la nueva consulta."""
    return [Turn(t.role, CITATION_RE.sub("", t.content)[:1200]) for t in history[-4:]]


def _classify(text: str, history: list[Turn]) -> tuple[str, str]:
    """Pregunta al modelo la intención. Si la respuesta no es válida, se trata como consulta normal."""
    context = "\n".join(f"{t.role}: {t.content[:400]}" for t in history)
    user = f"Conversation:\n{context or '(none)'}\n\nMessage: {text}"
    try:
        response = _client().chat(
            model=settings.OLLAMA_CHAT_MODEL,
            think=False,
            format=prompts.ROUTER_SCHEMA,
            keep_alive="30m",
            options={"temperature": 0, "num_ctx": settings.OLLAMA_NUM_CTX, "num_predict": 150},
            messages=[{"role": "system", "content": prompts.ROUTER_SYSTEM}, {"role": "user", "content": user}],
        )
        data = json.loads(response["message"]["content"])
        return data["intent"], data["standalone_query"].strip() or text
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        log.warning("Clasificador inválido: %s", exc)
        return "consulta", text


def route(text: str, history: list[Turn]) -> tuple[str, str]:
    """Decide la intención y, si hay conversación previa, reescribe el seguimiento.

    El historial hace cambiar de opinión a un modelo pequeño, y sin él un seguimiento parece ambiguo.
    Por eso se clasifica de las dos formas y basta una lectura de «consulta»: equivocarse hacia ese
    lado es seguro, porque la búsqueda en los PDF sigue filtrando lo que no tiene evidencia.
    """
    intent, query = _classify(text, [])
    if not history or intent == "sobre_asistente":
        return intent, query
    contextual, rewritten = _classify(text, history)
    if "consulta" in (intent, contextual):
        return "consulta", rewritten
    return contextual, rewritten


def _final(status: str, content: str, language: str, mode: str, citations=None) -> dict:
    return {
        "type": "final",
        "status": status,
        "content": content,
        "language": language,
        "mode": mode,
        "citations": citations or [],
        "suggestions": texts.suggestions(mode, language) if status == S.ANSWER else [],
        "model": settings.OLLAMA_CHAT_MODEL if citations else "",
    }


def _source_dict(number: int, hit: retrieval.Hit) -> dict:
    return {
        "number": number,
        "chunk_id": hit.chunk_id,
        "title": hit.title,
        "page": hit.page,
        "similarity": round(hit.similarity, 3),
        "quote": hit.text[:320],
    }


def validate_answer(text: str, available: int) -> tuple[str, set[int]]:
    """Elimina citas a fuentes inexistentes y devuelve el texto junto con las citas válidas."""
    used = {int(n) for n in CITATION_RE.findall(text) if 1 <= int(n) <= available}

    def keep(match):
        return match[0] if int(match[1]) in used else ""

    return CITATION_RE.sub(keep, text).strip(), used


def stream_answer(mode: str, language: str, question: str, hits, history) -> Iterator[str]:
    numbered = [(i, h.title, h.page, h.text) for i, h in enumerate(hits, start=1)]
    messages = [{"role": "system", "content": prompts.answer_system(mode, language)}]
    messages += [{"role": t.role, "content": t.content} for t in history]
    messages.append({"role": "user", "content": prompts.answer_user(question, numbered)})
    stream = _client().chat(
        model=settings.OLLAMA_CHAT_MODEL,
        think=False,
        stream=True,
        keep_alive="30m",
        options={"temperature": 0.2, "num_ctx": settings.OLLAMA_NUM_CTX, "num_predict": 1000},
        messages=messages,
    )
    for chunk in stream:
        if chunk["message"]["content"]:
            yield chunk["message"]["content"]


def run(text: str, mode: str, history: list[Turn], fallback_language: str) -> Iterator[dict]:
    """Genera eventos `status`, `sources`, `token` y un `final`."""
    language = lang_module.detect(text, fallback_language)
    plain = _plain(text)
    if len(plain.split()) <= 3:  # en saludos cortos la palabra indica el idioma mejor que el detector
        language = next((code for code, pattern in _WORD_LANGUAGE if pattern.match(plain)), language)
    yield {"type": "language", "language": language, "label": lang_module.LANGUAGE_LABEL[language]}

    if len(plain.split()) <= 6:
        if _FAREWELL.match(plain):
            yield _final(S.FAREWELL, texts.RESPONSES["farewell"][language], language, mode)
            return
        if _GREETING.match(plain):
            yield _final(S.GREETING, texts.RESPONSES["greeting"][language], language, mode)
            return

    try:
        history = clean_history(history)
        yield {"type": "status", "stage": "analyzing"}
        intent, query = route(text, history)
        query = query if history else text  # solo los seguimientos necesitan reescritura
        if intent == "sobre_asistente":
            yield _final(S.ABOUT, texts.RESPONSES["about"][language], language, mode)
            return

        yield {"type": "status", "stage": "searching"}
        hits = retrieval.search(query)
        best = retrieval.best_similarity(hits)
        log.info("intent=%s best=%.3f hits=%d query=%r", intent, best, len(hits), query[:80])

        # Una coincidencia fuerte en los PDF prevalece sobre un clasificador pequeño que puede equivocarse.
        if best < settings.RAG_STRONG_SIMILARITY:
            if intent == "fuera_de_dominio":
                yield _final(S.OUT_OF_DOMAIN, texts.RESPONSES["out_of_domain"][language], language, mode)
                return
            if intent == "ambigua":
                yield _final(S.AMBIGUOUS, texts.RESPONSES["ambiguous"][language], language, mode)
                return
        if best < settings.RAG_MIN_SIMILARITY:
            yield _final(S.NO_EVIDENCE, texts.RESPONSES["no_evidence"][language], language, mode)
            return

        sources = [_source_dict(i, h) for i, h in enumerate(hits, start=1)]
        yield {"type": "sources", "citations": sources}
        yield {"type": "status", "stage": "writing"}

        collected, released = "", False
        hold = len(prompts.NO_EVIDENCE) + 2
        for token in stream_answer(mode, language, query, hits, history):
            collected += token
            if not released:
                if len(collected.lstrip()) < hold and prompts.NO_EVIDENCE.startswith(collected.lstrip()[:len(prompts.NO_EVIDENCE)]):
                    continue
                released = True
                token = collected
            yield {"type": "token", "text": token}

        answer, used = validate_answer(collected, len(hits))
        if prompts.NO_EVIDENCE in answer or not used:
            yield _final(S.NO_EVIDENCE, texts.RESPONSES["no_evidence"][language], language, mode)
            return
        yield _final(S.ANSWER, answer, language, mode, [s for s in sources if s["number"] in used])
    except Exception:
        log.exception("Fallo al procesar la consulta")
        yield {"type": "error", "content": texts.RESPONSES["error"][language], "language": language}


