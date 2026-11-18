"""Flujo de CriterIA: idioma -> intención -> búsqueda en PDF -> ¿evidencia suficiente? -> respuesta o limitación."""
import json
import logging
import re
import unicodedata
from collections.abc import Iterator
from dataclasses import dataclass, field

import ollama
from django.conf import settings

from corpus import retrieval

from . import language as lang_module
from . import prompts, texts
from .models import Message

log = logging.getLogger(__name__)

CITATION_RE = re.compile(r"\[(\d{1,2})\]")
_REFUSAL = re.compile(
    r"\b(fuentes?|documentos?|textos?|corpus|sources?|documents?|texts?|fontes?)\b[^.\n]{0,80}\b(no|not|não|nao)\b[^.\n]{0,25}"
    r"(contien|mencion|inclu|abord|proporcion|cubr|defin|describ|explic|trat|habl|refier|identific|especific|indic|establec|determin"
    r"|contain|mention|includ|address|provid|cover|explain|discuss|talk|refer|identif|specif|rank|indicat|establish|determin"
    r"|cont[eê]m|forne|descrev|fala|trata)"
    r"|\bno (se encuentra|se encontr[oó]|hay|existe)\b[^.\n]{0,30}(informaci[oó]n|evidencia|datos|menci[oó]n|referencia)"
    r"|\b(there is )?no (information|evidence|mention)\b"
    r"|\bnão (há|existe|foi encontrad[ao]|se encontra)\b[^.\n]{0,30}(informa|evid[eê]ncia|men[cç])"
    r"|\b(no|not|não)\s+(es|está|is|are|son|é|são)\s+(mencionad|mentioned|abordad|addressed|cubiert|covered)",
    re.I,
)
_GREETING = re.compile(r"^(hola|holi|buenas|buenos dias|buenas tardes|buenas noches|saludos|hello|hi|hey|good (morning|afternoon|evening)|ola|oi|bom dia|boa tarde|boa noite)\b[\s!.,]*(.{0,20})$")
_FAREWELL = re.compile(r"^(adios|chao|chau|hasta (luego|pronto)|nos vemos|bye|goodbye|see you|tchau|ate (logo|breve|mais)|eso es todo|that'?s all|e isso|gracias,? (adios|hasta)|thanks,? bye|obrigad[oa],? tchau)\b")
_WORD_LANGUAGE = (
    ("pt", re.compile(r"^(ola|oi|bom dia|boa tarde|boa noite|tchau|ate (logo|breve|mais)|obrigad[oa])\b")),
    ("es", re.compile(r"^(hola|holi|buenas|buenos dias|saludos|adios|chao|chau|hasta|nos vemos|gracias)\b")),
    ("en", re.compile(r"^(hello|hi|hey|good (morning|afternoon|evening)|bye|goodbye|see you|thanks)\b")),
)
S = Message.Status

# Pedidos de idioma, también informales: «tradúcelo», «ahora en inglés», «pásalo a portugués», «inglés porfa»,
# «y en portugués?», «answer in Spanish», «agora em português». Se comparan sin tildes ni mayúsculas.
_LANGUAGE_NAMES = {
    "es": r"espanol|espaniol|espanhol|castellano|spanish",
    "en": r"ingles|ingle|inglish|english|englis",
    "pt": r"portugues|portuges|portuguese|portugese",
}
_TARGET_LANGUAGE = tuple(
    (code, re.compile(rf"\b(?:en|al|a|in|into|to|em|para|pro|pal)\s+(?:el\s+|o\s+)?(?:{names})\b"))
    for code, names in _LANGUAGE_NAMES.items()
)
_LANGUAGE_NAME_WORD = re.compile("|".join(_LANGUAGE_NAMES.values()))
_BARE_LANGUAGE = tuple((code, re.compile(rf"\b(?:{names})\b")) for code, names in _LANGUAGE_NAMES.items())
_LANGUAGE_VERB = re.compile(
    r"\b(tradu\w*|translat\w*|respond\w*|contest\w*|answer\w*|reply|repit\w*|repet\w*|repeat\w*|dime\w*|dilo|di|say|tell"
    r"|explica\w*|explain\w*|escrib\w*|escrev\w*|write|ahora|now|agora|puedes|podrias|can you|could you|pode|poderia"
    r"|version|versao|hazlo|faz|fala|habla\w*|speak|quiero|quisiera|quero|want|need|necesito|preciso|pas[ae]\w*|pon\w*"
    r"|dame|give|cambi\w*|change|switch|mud\w*|coloc\w*|lee\w*|read|cuent\w*|manda\w*|send)\b"
)
# Muletillas que no cambian el pedido: «inglés porfa», «ok, en portugués», «y ahora en español».
_FILLER = re.compile(r"\b(y|and|e|pls|plis|please|porfa|porfavor|por favor|pf|ok|okay|vale|bueno|ahora|now|agora|mejor"
                     r"|better|melhor|tambien|also|tambem|ya|then|entonces|entao|lo|la|it|isso|eso)\b")
# «Tradúcelo» sin idioma: el verbo indica a qué idioma.
_TRANSLATE_ONLY = (
    ("en", re.compile(r"\btranslat\w*")),
    ("pt", re.compile(r"\btraduz\b|\btraduza\w*|\btraduzir\w*")),
    ("es", re.compile(r"\btraduc\w*|\btraduzc\w*")),
)
_REFERS_BACK = re.compile(r"\b(anterior|previa|previo|previous|last|ultima|ultimo|eso|esto|esa|that|this|it|isso|isto|essa|respuesta|answer|resposta|lo|la)\b")
_SEGMENT = re.compile(r"(?<=[?!.:])\s+|\n+")


@dataclass
class Turn:
    role: str
    content: str
    citations: list = field(default_factory=list)  # fuentes de una respuesta, para poder traducirla tal cual


def _plain(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower().strip())
    return "".join(c for c in decomposed if not unicodedata.combining(c)).lstrip("¡¿ ")


def _client() -> ollama.Client:
    return ollama.Client(host=settings.OLLAMA_HOST, timeout=settings.OLLAMA_TIMEOUT)


def clean_history(history: list[Turn]) -> list[Turn]:
    """Últimos turnos sin marcas [n], que no corresponden a las fuentes de la nueva consulta."""
    return [Turn(t.role, CITATION_RE.sub("", t.content)[:1200]) for t in history[-4:]]


def _classify(text: str, history: list[Turn]) -> tuple[str, str, str]:
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
        return data["intent"], data["standalone_query"].strip() or text, data.get("spanish_query", "").strip()
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        log.warning("Clasificador inválido: %s", exc)
        return "consulta", text, ""


# Preguntas explícitas sobre el asistente, que no dependen del tema de la conversación.
_ABOUT_ASSISTANT = re.compile(
    r"^(quien eres|que eres|quien sos|que sos|que puedes hacer|que sabes hacer|como funcionas|para que sirves|presentate"
    r"|who are you|what are you|what can you do|how do you work|what do you do|introduce yourself"
    r"|quem e voce|quem es tu|o que voce (pode fazer|faz|e)|como voce funciona|para que (voce )?serve)\b"
)


_NAMES_ASSISTANT = re.compile(r"\b(criteria|asistente|assistant|assistente|chatbot|bot|eres|sos|are you|voce e|tu es)\b")


def route(text: str, history: list[Turn]) -> tuple[str, str, str]:
    """Decide la intención y, si hay conversación previa, reescribe el seguimiento.

    El historial hace cambiar de opinión a un modelo pequeño, y sin él un seguimiento parece ambiguo.
    Por eso se clasifica de las dos formas y basta una lectura de «consulta»: equivocarse hacia ese
    lado es seguro, porque la búsqueda en los PDF sigue filtrando lo que no tiene evidencia.
    Dentro de una conversación, una pregunta con «tú» («¿qué es lo más importante que deberías saber?»)
    sigue el tema; solo «¿quién eres?» y similares hablan del asistente.
    """
    intent, query, spanish = _classify(text, [])
    explicit = _ABOUT_ASSISTANT.match(_plain(text)) or _NAMES_ASSISTANT.search(_plain(text))
    if not history:
        # «¿Qué es lo más importante que debería saber de la Constitución?» no es una pregunta sobre el asistente.
        return ("consulta" if intent == "sobre_asistente" and not explicit else intent), query, spanish
    if intent == "sobre_asistente" and explicit:
        return intent, query, spanish
    contextual, rewritten, spanish = _classify(text, history)
    if "consulta" in (intent, contextual):
        return "consulta", rewritten, spanish
    if "sobre_asistente" in (intent, contextual):
        # El modelo no lo leyó como seguimiento: se busca también con la última pregunta para no perder el tema.
        topic = next((t.content for t in reversed(history) if t.role == "user"), "")
        return "consulta", f"{rewritten} ({topic})" if topic and topic not in rewritten else rewritten, spanish
    return contextual, rewritten, spanish


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


# Países extranjeros: si la pregunta nombra uno y ningún fragmento lo menciona, esos fragmentos no
# pueden responderla, y un modelo pequeño tiende a atribuirle leyes de otro país. Argentina no está
# porque es la jurisdicción de todo el corpus: sus leyes no siempre repiten el nombre del país.
_COUNTRIES = (
    ("mexic",), ("brasil", "brazil"), ("ecuador", "ecuatori", "equator", "equatori"), ("chile",), ("colombia",),
    ("peru",), ("uruguay", "uruguai"), ("paraguay", "paraguai"), ("bolivia",), ("venezuel",), ("cuba",),
    ("espana", "spain", "espanha"), ("estados unidos", "united states", "eeuu", "ee uu", "estadounidense"),
    ("reino unido", "united kingdom", "britan", "british"), ("francia", "france"), ("alemania", "germany", "alemanha"),
    ("italia", "italy"), ("china",), ("japon", "japan", "japao"), ("canada",), ("australia",), ("europ",),
)
_ACRONYM = re.compile(r"\b[A-ZÁÉÍÓÚÑ]{2,7}\b")
_COMMON_CAPS = {"IA", "AI", "TV", "PDF", "ONG", "NGO", "OK", "APA", "URL", "LLM", "EEUU", "UU", "EE"}


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))


def missing_entity(question: str, hits) -> str | None:
    """Devuelve el país o la sigla que la pregunta nombra y que no aparece en ningún fragmento."""
    sources = " ".join(f"{h.title} {h.text}" for h in hits)
    folded_question, folded_sources = _fold(question), _fold(sources)
    for stems in _COUNTRIES:
        if any(re.search(rf"\b{re.escape(s)}", folded_question) for s in stems):
            if not any(re.search(rf"\b{re.escape(s)}", folded_sources) for s in stems):
                return stems[0]
    words = question.split()
    if sum(w.isupper() for w in words) * 2 < len(words):  # en un texto escrito en mayúsculas no hay siglas fiables
        for acronym in _ACRONYM.findall(question):
            if acronym not in _COMMON_CAPS and acronym not in sources:
                return acronym
    return None


def looks_like_refusal(answer: str) -> bool:
    """El modelo a veces explica que no hay información en lugar de usar NO_EVIDENCE.
    Solo se revisa la primera sección: en «Vacíos de evidencia» esas frases son legítimas."""
    sections = re.split(r"(?m)^#{2,4} ", answer)
    head = "".join(sections[:2]) if len(sections) > 1 else answer
    return bool(_REFUSAL.search(head[:400]))


def drop_refusals(answer: str) -> str:
    """Quita las frases que dicen «las fuentes no contienen…» y no citan nada, junto con los títulos que
    quedan vacíos. Se usa cuando el resto de la respuesta sí está fundamentado en varias fuentes."""
    lines = []
    for line in answer.splitlines():
        sentences = re.split(r"(?<=[.!?])\s+", line)
        kept = [s for s in sentences if CITATION_RE.search(s) or not _REFUSAL.search(s)]
        line = " ".join(kept)
        if line.strip() or not lines or lines[-1].strip():
            lines.append(line if line.strip() else "")
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"(?m)^#{1,4} [^\n]*\n\s*(?=^#{1,4} |\Z)", "", cleaned)  # títulos sin contenido
    cleaned = re.sub(r"(?m)^\s*(?:[-*]|\d+[.)])\s*$\n?", "", cleaned)  # viñetas vacías
    return cleaned.strip()


def validate_answer(text: str, available: int) -> tuple[str, set[int]]:
    """Elimina citas a fuentes inexistentes y devuelve el texto junto con las citas válidas."""
    used = {int(n) for n in CITATION_RE.findall(text) if 1 <= int(n) <= available}

    def keep(match):
        return match[0] if int(match[1]) in used else ""

    return CITATION_RE.sub(keep, text).strip(), used


def stream_answer(mode: str, language: str, question: str, hits, voice: bool = False, style: str | None = None) -> Iterator[str]:
    """Redacta sin el historial: la pregunta ya llega reescrita como autónoma, y con la respuesta
    anterior delante el modelo tiende a copiarla, incluso en otro idioma.
    En modo voz la respuesta es corta y conversacional; en el chat escrito no cambia nada."""
    numbered = [(i, h.title, h.page, h.text) for i, h in enumerate(hits, start=1)]
    messages = [
        {"role": "system", "content": prompts.answer_system(mode, language, voice, style)},
        {"role": "user", "content": prompts.answer_user(question, numbered, language, style)},
    ]
    stream = _client().chat(
        model=settings.OLLAMA_CHAT_MODEL,
        think=False,
        stream=True,
        keep_alive="30m",
        # Semilla fija: la misma pregunta con las mismas fuentes recibe siempre la misma respuesta.
        options={"temperature": 0.2, "seed": 42, "num_ctx": settings.OLLAMA_NUM_CTX, "num_predict": 320 if voice else {"brief": 350, "detail": 1400}.get(style, 1000)},
        messages=messages,
    )
    for chunk in stream:
        if chunk["message"]["content"]:
            yield chunk["message"]["content"]


def in_language(answer: str, language: str) -> bool:
    plain = re.sub(r"[#*_>`-]+", " ", CITATION_RE.sub("", answer))
    if len(plain.split()) < 6:  # demasiado corto para juzgar
        return True
    return lang_module.dominant(plain) == language


def translate_answer(answer: str, language: str) -> str:
    response = _client().chat(
        model=settings.OLLAMA_CHAT_MODEL,
        think=False,
        keep_alive="30m",
        options={"temperature": 0, "num_ctx": settings.OLLAMA_NUM_CTX, "num_predict": 1400},
        messages=[
            {"role": "system", "content": prompts.TRANSLATE_SYSTEM.format(language=prompts.LANGUAGE_NAME[language])},
            {"role": "user", "content": answer},
        ],
    )
    return response["message"]["content"]


def _language_in_segment(folded: str) -> str | None:
    """Idioma pedido en un fragmento del mensaje, o None si el fragmento no es un pedido de idioma."""
    words = re.sub(r"[^\w\s]", " ", folded).split()
    core = _FILLER.sub(" ", " ".join(words)).split()
    found = next((code for code, pattern in _TARGET_LANGUAGE if pattern.search(folded)), None)
    if not found and len(core) <= 2:  # «inglés», «english please», «y portugués?»
        found = next((code for code, pattern in _BARE_LANGUAGE if pattern.search(folded)), None)
    if found and (_LANGUAGE_VERB.search(folded) or len(core) <= 3 or any(p.search(folded) for _, p in _STYLE_PATTERNS)):
        return found
    if not found and len(words) <= 6:  # «tradúcelo», «translate it», «traduz isso»
        return next((code for code, pattern in _TRANSLATE_ONLY if pattern.search(folded)), None)
    return None


def language_request(text: str) -> tuple[str | None, str]:
    """Detecta si el mensaje pide un idioma de respuesta y separa la pregunta que trae, si trae una.

    Devuelve (idioma pedido o None, pregunta incluida o "" si se refiere a la respuesta anterior).
    """
    segments = [part.strip() for part in _SEGMENT.split(text.strip()) if part.strip()]
    target, rest = None, []
    for segment in segments:
        folded = _fold(segment)
        found = _language_in_segment(folded)
        if found:
            target = found
            # Una sola frase larga sin referencia a lo anterior: la pregunta va junto al pedido de idioma.
            if len(segments) == 1 and len(folded.split()) > 10 and not _REFERS_BACK.search(folded):
                pattern = dict(_TARGET_LANGUAGE)[found]
                rest.append(pattern.sub(" ", folded).strip())
            continue
        rest.append(segment)
    if not target:
        return None, ""
    question = " ".join(rest).strip()
    return target, question if len(question.split()) >= 3 else ""


# Instrucciones informales sobre la respuesta: «solo lo más importante», «resúmelo», «más simple»,
# «dame un ejemplo», «más detalle», «en lista». Se comparan sin tildes ni mayúsculas.
_STYLE_PATTERNS = (
    ("brief", re.compile(
        r"\b(lo (mas|realmente) (importante|relevante|esencial|basico)|lo principal|lo esencial|lo clave|lo fundamental"
        r"|resum\w*|mas cort\w*|mas breve|brevemente|breve|en pocas palabras|en una (frase|linea|oracion)|en corto|sintetiz\w*"
        r"|in short|summar\w*|shorter|briefly|key points?|main points?|most important|tl ?dr|in a nutshell|in one sentence"
        r"|o (mais )?importante|o principal|o essencial|mais curto|em poucas palavras|em uma frase)\b")),
    ("simple", re.compile(
        r"\b(mas (simple|sencill\w*|facil|claro)|en palabras (simples|sencillas)|de forma (simple|sencilla)|sencillito"
        r"|no (lo )?entiendo|no entendi|como si tuviera|para (un|una) (nino|nina|principiante)|simpler|simple words"
        r"|plain (words|language)|explain like|eli5|i don.?t understand|mais simples|mais facil|nao entendi|nao entendo"
        r"|palavras simples)\b")),
    ("detail", re.compile(
        r"\b(mas detalle\w*|con detalle|en detalle|ampli(a|alo|ar|ame)|profundiz\w*|desarrolla\w*|explica(lo|me)? mas"
        r"|mas a fondo|more detail\w*|in detail|expand\w*|elaborate|go deeper|mais detalhes|em detalhes|aprofund\w*"
        r"|explique mais)\b")),
    ("examples", re.compile(r"\b(un ejemplo|ejemplos|ejemplific\w*|an example|examples|for example|um exemplo|exemplos)\b")),
    ("list", re.compile(r"\b(en (una )?lista|en (vinetas|puntos)|enumera\w*|punto por punto|as a list|bullet\w*|in points"
                        r"|em (lista|topicos))\b")),
)
# Palabras que no aportan tema: si al quitarlas no queda casi nada, la instrucción es sobre la respuesta anterior.
_STYLE_FILLER = re.compile(
    r"\b(no|si|yes|solo|solamente|nomas|just|only|apenas|pero|but|mas|more|y|and|e|ok|okay|vale|bueno|ahora|now|agora"
    r"|por|favor|porfa|pls|please|plis|dime\w*|dame|da|give|tell|me|puedes|podrias|can|could|you|pode|poderia|quiero"
    r"|want|quero|necesito|need|hazlo|haz|make|do|faz|explica\w*|explain\w*|explique|cuenta\w*|di|dilo|say|eso|esto"
    r"|it|that|this|isso|isto|lo|la|el|los|las|le|un|una|unos|a|an|the|o|os|um|uma|de|del|do|da|of|sobre|about|acerca"
    r"|respuesta|answer|resposta|anterior|previous|otra|vez|again|de nuevo|en|in|em|con|with|com|para|for|que|what|cual"
    r"|is|es|son|are|e|forma|manera|way|mucho|much|muy|very|realmente|really|algo|something|tema|topic"
    r"|deja\w*|deixa\w*|leave|keep|show|muestra\w*|mostra\w*|part|parts|parte|partes|thing|things|cosa|cosas|coisa|coisas"
    r"|bit|poco|pouco|point|points|punto|puntos|ponto|pontos|nada|nothing|todo|all|tudo)\b"
)


def style_request(text: str) -> tuple[str | None, str]:
    """Detecta una instrucción sobre cómo responder. Devuelve (estilo o None, pregunta nueva o "").

    «No, solo lo más importante» se refiere a la respuesta anterior (sin pregunta nueva); en cambio
    «lo más importante de la ley de datos personales» trae su propio tema y se responde como pregunta.
    """
    folded = _fold(text)
    for style, pattern in _STYLE_PATTERNS:
        if pattern.search(folded):
            remainder = _STYLE_FILLER.sub(" ", re.sub(r"[^\w\s]", " ", pattern.sub(" ", folded)))
            words = [w for w in remainder.split() if not _LANGUAGE_NAME_WORD.fullmatch(w)]
            return style, text.strip() if len(words) >= 2 else ""
    return None, ""


def _previous_question(history: list[Turn]) -> tuple[str, list[Turn], str | None] | None:
    """Última pregunta real, el contexto que la precedía y la instrucción de formato más reciente.

    Los pedidos anteriores («ahora en español», «solo lo más importante») no son preguntas: se sigue hacia
    atrás hasta la que los originó. La última instrucción de formato se hereda, así «solo lo más importante»
    seguido de «ponlo en inglés» da lo más importante, en inglés.
    """
    inherited = None
    for index in range(len(history) - 1, -1, -1):
        if history[index].role != "user":
            continue
        target, embedded = language_request(history[index].content)
        style, styled = style_request(history[index].content)
        inherited = inherited or style
        if not target and not style:
            return history[index].content, history[:index], inherited
        if embedded or styled:
            return embedded or styled, history[:index], inherited
    return None


# Palabras típicas para distinguir español de portugués en instrucciones cortas, donde el detector duda.
_ES_MARKS = re.compile(r"\b(lo|el|los|las|del|dejame|dame|ahora|quiero|puedes|podrias|eso|esto|pero|muy|mas|y|solo|hazlo|ponlo|dilo|dime|explicalo|porfa|que)\b")
_PT_MARKS = re.compile(r"\b(o|os|as|do|da|dos|das|me deixa|deixa|so|agora|quero|pode|poderia|voce|isso|isto|mas e|muito|mais|nao|faz|coloca|fala|me diz|explica isso)\b")


def _instruction_language(text: str, fallback: str) -> str:
    """Idioma de una instrucción. El inglés se detecta bien; entre español y portugués, en mensajes cortos,
    deciden las palabras típicas de cada uno y, si empatan, el idioma de la conversación."""
    words = text.split()
    if len(words) < 3:
        return fallback
    detected = lang_module.detect(text, fallback)
    if detected not in ("es", "pt") or len(words) >= 8:
        return detected
    folded = _fold(text)
    spanish, portuguese = len(_ES_MARKS.findall(folded)), len(_PT_MARKS.findall(folded))
    if spanish != portuguese:
        return "es" if spanish > portuguese else "pt"
    return fallback if fallback in ("es", "pt") else detected


def _translate_previous(answer: Turn, language: str, mode: str) -> dict | None:
    """Traduce la última respuesta conservando sus marcas [n]; None si la traducción no es fiable."""
    numbers = {c["number"] for c in answer.citations}
    try:
        translated, used = validate_answer(translate_answer(answer.content, language), max(numbers))
    except Exception:
        log.exception("No se pudo traducir la respuesta anterior")
        return None
    used &= numbers
    if not used or not in_language(translated, language) or prompts.NO_EVIDENCE in translated:
        log.info("Traducción descartada: se vuelve a responder la pregunta")
        return None
    return _final(S.ANSWER, translated, language, mode, [c for c in answer.citations if c["number"] in used])


def run(text: str, mode: str, history: list[Turn], fallback_language: str, voice: bool = False) -> Iterator[dict]:
    """Genera eventos `status`, `sources`, `token` y un `final`. `voice` pide la versión hablada."""
    target, embedded = language_request(text)
    style, styled = style_request(text)
    asked_in = _instruction_language(text, fallback_language)
    if target and not style and not embedded and history and history[-1].role == "assistant" and history[-1].citations:
        # «Ahora en inglés» / «tradúcelo»: se traduce la última respuesta tal cual, con sus mismas citas y fuentes.
        # Es fiel a lo que el usuario ve y más rápido que redactar de nuevo. Si la traducción pierde las citas
        # o no queda en el idioma pedido, se sigue con el camino normal (volver a responder la pregunta).
        yield {"type": "language", "language": target, "label": lang_module.LANGUAGE_LABEL[target]}
        yield {"type": "status", "stage": "translating"}
        translated = _translate_previous(history[-1], target, mode)
        if translated:
            yield translated
            return
    if target or style:
        # «Tradúcelo», «ahora en inglés», «solo lo más importante», «más simple»…: se aplica a la pregunta
        # incluida o, si no hay, a la anterior. Se vuelve a buscar en los PDF: la respuesta conserva fuentes y citas.
        new_question = embedded or styled
        if new_question:
            text = new_question
        else:
            previous = _previous_question(history)
            if previous is None:
                reply = target or asked_in
                yield {"type": "language", "language": reply, "label": lang_module.LANGUAGE_LABEL[reply]}
                yield _final(S.AMBIGUOUS, texts.RESPONSES["ambiguous"][reply], reply, mode)
                return
            text, history, inherited = previous
            style = style or inherited
    question_language = lang_module.detect(text, fallback_language)
    # Sin idioma pedido, se responde en el idioma en que se dio la instrucción.
    language = target or (asked_in if style else question_language)
    plain = _plain(text)
    if not target and len(plain.split()) <= 3:  # en saludos cortos la palabra indica el idioma mejor que el detector
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
        intent, query, spanish = route(text, history)
        query = query if history else text  # solo los seguimientos necesitan reescritura
        queries = [query]
        if question_language != "es" and spanish and spanish.lower() != query.lower():
            queries.append(spanish)  # los PDF están en español: buscar también con la traducción
        if intent == "sobre_asistente":
            yield _final(S.ABOUT, texts.RESPONSES["about"][language], language, mode)
            return

        yield {"type": "status", "stage": "searching"}
        hits = retrieval.search(queries)
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

        missing = missing_entity(text, hits)
        if missing:
            log.info("Sin evidencia: la pregunta nombra %r y ningún fragmento lo menciona", missing)
            yield _final(S.NO_EVIDENCE, texts.RESPONSES["no_evidence"][language], language, mode)
            return

        sources = [_source_dict(i, h) for i, h in enumerate(hits, start=1)]
        yield {"type": "sources", "citations": sources}
        yield {"type": "status", "stage": "writing"}

        collected, released = "", False
        hold = len(prompts.NO_EVIDENCE) + 2
        for token in stream_answer(mode, language, query, hits, voice=voice, style=style):
            collected += token
            if not released:
                if len(collected.lstrip()) < hold and prompts.NO_EVIDENCE.startswith(collected.lstrip()[:len(prompts.NO_EVIDENCE)]):
                    continue
                released = True
                token = collected
            yield {"type": "token", "text": token}

        answer, used = validate_answer(collected, len(hits))
        if prompts.NO_EVIDENCE not in answer and len(used) >= 2 and looks_like_refusal(answer):
            # Abre diciendo «las fuentes no contienen…» pero luego responde con varias citas (pasa sobre todo al
            # redactar en otro idioma): se quita esa frase en lugar de descartar una respuesta fundamentada.
            answer, used = validate_answer(drop_refusals(answer), len(hits))
        if prompts.NO_EVIDENCE in answer or not used or looks_like_refusal(answer):
            yield _final(S.NO_EVIDENCE, texts.RESPONSES["no_evidence"][language], language, mode)
            return
        if not in_language(answer, language):  # se responde siempre en el idioma de la pregunta
            yield {"type": "status", "stage": "translating"}
            translated, translated_used = validate_answer(translate_answer(answer, language), len(hits))
            if translated_used and in_language(translated, language):
                answer, used = translated, translated_used
            else:
                log.warning("No se pudo traducir la respuesta a %s", language)
        yield _final(S.ANSWER, answer, language, mode, [s for s in sources if s["number"] in used])
    except Exception:
        log.exception("Fallo al procesar la consulta")
        yield {"type": "error", "content": texts.RESPONSES["error"][language], "language": language}


