"""Textos fijos del flujo. Dependen del idioma detectado en cada mensaje, no del idioma de la interfaz."""
from django.conf import settings

_NAME = settings.ASSISTANT_NAME

RESPONSES = {
    "greeting": {
        "es": f"¡Hola! Soy **{_NAME}**, tu asistente para explorar dilemas éticos en inteligencia artificial, comunicación y periodismo. ¿Qué te gustaría hacer hoy: consultar un concepto, explorar casos o analizar un dilema?",
        "en": f"Hi! I'm **{_NAME}**, your assistant for exploring ethical dilemmas in artificial intelligence, communication and journalism. What would you like to do today: look up a concept, explore cases or analyze a dilemma?",
        "pt": f"Olá! Sou **{_NAME}**, seu assistente para explorar dilemas éticos em inteligência artificial, comunicação e jornalismo. O que você gostaria de fazer hoje: consultar um conceito, explorar casos ou analisar um dilema?",
    },
    "about": {
        "es": f"Soy **{_NAME}**, un asistente de consulta sobre ética en inteligencia artificial, comunicación y periodismo. Puedo **explicar conceptos**, **presentar casos** y **analizar dilemas éticos**. Respondo únicamente con los documentos PDF del corpus del proyecto, indico la fuente de cada afirmación y te aviso cuando no tengo evidencia suficiente.",
        "en": f"I'm **{_NAME}**, a reference assistant on ethics in artificial intelligence, communication and journalism. I can **explain concepts**, **present cases** and **analyze ethical dilemmas**. I answer only from the project's PDF corpus, cite the source of each statement and tell you when I lack enough evidence.",
        "pt": f"Sou **{_NAME}**, um assistente de consulta sobre ética em inteligência artificial, comunicação e jornalismo. Posso **explicar conceitos**, **apresentar casos** e **analisar dilemas éticos**. Respondo apenas com os documentos PDF do corpus do projeto, indico a fonte de cada afirmação e aviso quando não tenho evidências suficientes.",
    },
    "farewell": {
        "es": f"¡Gracias por conversar con {_NAME}! Recuerda que una decisión responsable comienza con una reflexión informada. ¡Hasta pronto!",
        "en": f"Thank you for talking with {_NAME}! Remember that a responsible decision begins with informed reflection. See you soon!",
        "pt": f"Obrigado por conversar com {_NAME}! Lembre-se de que uma decisão responsável começa com uma reflexão informada. Até breve!",
    },
    "ambiguous": {
        "es": "Necesito un poco más de información para ayudarte. ¿Puedes indicarme el tema, la situación concreta o el principio ético que te interesa?",
        "en": "I need a bit more information to help you. Could you tell me the topic, the specific situation or the ethical principle you are interested in?",
        "pt": "Preciso de um pouco mais de informação para ajudar. Você pode indicar o tema, a situação concreta ou o princípio ético que lhe interessa?",
    },
    "out_of_domain": {
        "es": "Esa consulta está fuera de mi ámbito. Solo respondo sobre ética, inteligencia artificial, comunicación y periodismo. ¿Quieres plantear una pregunta sobre alguno de estos temas?",
        "en": "That question is outside my scope. I only answer about ethics, artificial intelligence, communication and journalism. Would you like to ask about one of these topics?",
        "pt": "Essa pergunta está fora do meu âmbito. Só respondo sobre ética, inteligência artificial, comunicação e jornalismo. Quer fazer uma pergunta sobre algum desses temas?",
    },
    "no_evidence": {
        "es": "No tengo información suficiente en los documentos del corpus para responder con fundamento, y prefiero no inventar una respuesta. Prueba a reformular tu consulta o a preguntar por otro aspecto del tema.",
        "en": "I don't have enough information in the corpus documents to give a grounded answer, and I'd rather not make one up. Try rephrasing your question or asking about another aspect of the topic.",
        "pt": "Não tenho informações suficientes nos documentos do corpus para responder com fundamento e prefiro não inventar uma resposta. Tente reformular a pergunta ou perguntar sobre outro aspecto do tema.",
    },
    "error": {
        "es": "Ocurrió un problema al procesar tu consulta. Por favor, inténtalo nuevamente.",
        "en": "Something went wrong while processing your question. Please try again.",
        "pt": "Ocorreu um problema ao processar sua consulta. Por favor, tente novamente.",
    },
}

_S = {
    "A": [
        {"es": ("¿Con qué principios éticos se relaciona?", "A"), "en": ("Which ethical principles does it relate to?", "A"), "pt": ("Com quais princípios éticos se relaciona?", "A")},
        {"es": ("Buscar casos sobre este concepto", "B"), "en": ("Find cases about this concept", "B"), "pt": ("Buscar casos sobre este conceito", "B")},
    ],
    "B": [
        {"es": ("Analizar este caso como dilema ético", "C"), "en": ("Analyze this case as an ethical dilemma", "C"), "pt": ("Analisar este caso como dilema ético", "C")},
        {"es": ("¿Qué consecuencias tuvo?", "B"), "en": ("What were the consequences?", "B"), "pt": ("Quais foram as consequências?", "B")},
    ],
    "C": [
        {"es": ("Profundiza en las consecuencias de cada alternativa", "C"), "en": ("Go deeper into the consequences of each alternative", "C"), "pt": ("Aprofunde as consequências de cada alternativa", "C")},
        {"es": ("Buscar casos similares", "B"), "en": ("Find similar cases", "B"), "pt": ("Buscar casos semelhantes", "B")},
    ],
}
_END = {"es": "Terminar conversación", "en": "End conversation", "pt": "Encerrar conversa"}
_BYE = {"es": "Adiós", "en": "Goodbye", "pt": "Tchau"}


def suggestions(mode: str, language: str) -> list[dict]:
    items = [{"label": s[language][0], "prompt": s[language][0], "mode": s[language][1]} for s in _S[mode]]
    items.append({"label": _END[language], "prompt": _BYE[language], "mode": mode, "end": True})
    return items
