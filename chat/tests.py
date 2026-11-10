import base64
import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from corpus.retrieval import Hit

from . import language, media, pipeline
from .models import Conversation, GeneratedAsset, Message

User = get_user_model()
HIT = Hit(chunk_id=1, document_id=1, page=4, title="Doc", text="La transparencia exige informar.", similarity=0.8)
WEAK = Hit(chunk_id=1, document_id=1, page=4, title="Doc", text="texto", similarity=0.45)


def run(text="¿Debo avisar a la audiencia si usé inteligencia artificial?", mode="A", intent="consulta", hits=(HIT,), tokens=("### Definición\nHay que informar [1]",)):
    with patch("chat.pipeline.route", return_value=(intent, text, text)), \
         patch("chat.pipeline.retrieval.search", return_value=list(hits)), \
         patch("chat.pipeline.stream_answer", return_value=iter(tokens)) as answer:
        events = list(pipeline.run(text, mode, [], "es"))
    return events, events[-1], answer


class FirstTurnQueryTests(SimpleTestCase):
    def test_first_message_is_not_rewritten_but_followups_are(self):
        with patch("chat.pipeline.route", return_value=("consulta", "reescrita", "reescrita")), \
             patch("chat.pipeline.retrieval.search", return_value=[]) as search:
            list(pipeline.run("Pregunta original sobre periodismo y ética", "A", [], "es"))
            search.assert_called_with(["Pregunta original sobre periodismo y ética"])
            list(pipeline.run("¿y sus consecuencias entonces?", "A", [pipeline.Turn("user", "x")], "es"))
            search.assert_called_with(["reescrita"])

    def test_non_spanish_questions_also_search_in_spanish(self):
        with patch("chat.pipeline.route", return_value=("consulta", "x", "¿Se pueden tratar datos sin consentimiento?")), \
             patch("chat.pipeline.retrieval.search", return_value=[]) as search:
            list(pipeline.run("Can personal data be processed without consent in Argentina?", "A", [], "es"))
        search.assert_called_with(["Can personal data be processed without consent in Argentina?", "¿Se pueden tratar datos sin consentimiento?"])


class ShortMessageLanguageTests(SimpleTestCase):
    def test_single_word_greetings_set_their_own_language(self):
        for text, expected in (("Tchau", "pt"), ("Olá", "pt"), ("Adiós", "es"), ("Goodbye", "en"), ("Hi", "en")):
            events = list(pipeline.run(text, "A", [], "es" if expected != "es" else "en"))
            self.assertEqual(events[-1]["language"], expected, text)


class LanguageTests(SimpleTestCase):
    def test_detects_the_three_supported_languages(self):
        self.assertEqual(language.detect("¿Debo avisar a la audiencia si la nota fue redactada con ayuda?"), "es")
        self.assertEqual(language.detect("Should the newsroom disclose that the article was written with AI?"), "en")
        self.assertEqual(language.detect("Devo avisar o público que a matéria foi escrita com ajuda da inteligência artificial?"), "pt")

    def test_short_text_keeps_conversation_language(self):
        self.assertEqual(language.detect("ok", "pt"), "pt")


@override_settings(RAG_MIN_SIMILARITY=0.5, RAG_STRONG_SIMILARITY=0.62)
class FlowTests(SimpleTestCase):
    def test_grounded_answer_keeps_valid_citations(self):
        _, final, _ = run()
        self.assertEqual(final["status"], "answer")
        self.assertEqual([c["number"] for c in final["citations"]], [1])
        self.assertEqual(final["citations"][0]["page"], 4)

    def test_answer_without_citations_becomes_limitation(self):
        _, final, _ = run(tokens=("Hay que informar sin decir de dónde sale.",))
        self.assertEqual((final["status"], final["citations"]), ("no_evidence", []))

    def test_invented_citation_numbers_are_removed(self):
        _, final, _ = run(tokens=("Informar [1] y sancionar [9].",))
        self.assertEqual(final["content"], "Informar [1] y sancionar .")

    def test_model_sentinel_becomes_limitation_and_is_not_streamed(self):
        events, final, _ = run(tokens=("NO_", "EVIDENCE",))
        self.assertEqual(final["status"], "no_evidence")
        self.assertFalse([e for e in events if e["type"] == "token"])

    def test_answer_that_only_says_there_is_no_information_becomes_limitation(self):
        for text in ("### Definition\nThe provided sources do not contain information about the BBC [1].",
                     "### Definición\nNo se encuentra información en las fuentes sobre la UNESCO [1].",
                     "### Definição\nA lei argentina exige consentimento [1]. A LGPD brasileira não é mencionada nas fontes."):
            self.assertEqual(run(tokens=(text,))[1]["status"], "no_evidence", text)

    def test_more_refusal_phrasings(self):
        for text in ("### Definición\nNo existe evidencia en las fuentes que mencione una ley ecuatoriana [1].",
                     "### Definition\nThere is no information about drones in the sources [1]."):
            self.assertEqual(run(tokens=(text,))[1]["status"], "no_evidence", text)

    def test_foreign_country_or_acronym_absent_from_sources_is_no_evidence(self):
        argentine = Hit(chunk_id=1, document_id=1, page=3, title="Ley 25.326", text="El tratamiento de datos requiere consentimiento. AFSCA.", similarity=0.8)
        for question in ("¿Qué sanciones impone la ley mexicana a las noticias falsas?",
                         "O que diz a LGPD brasileira sobre consentimento?",
                         "How does the BBC label AI content?"):
            _, final, answer = run(text=question, hits=(argentine,))
            self.assertEqual(final["status"], "no_evidence", question)
            answer.assert_not_called()

    def test_entities_present_in_sources_or_common_are_allowed(self):
        argentine = Hit(chunk_id=1, document_id=1, page=3, title="Ley 26.522", text="La AFSCA aplica la ley. Unión Europea y Canadá regulan.", similarity=0.8)
        for question in ("¿Qué funciones tiene la AFSCA?", "¿Qué dice la ley argentina sobre la IA?",
                         "¿Cómo regula Canadá a las plataformas?", "¿Qué dice el reglamento europeo?",
                         "QUÉ ES LA LEY DE MEDIOS Y CÓMO SE APLICA"):
            self.assertEqual(run(text=question, hits=(argentine,), tokens=("Dato [1]",))[1]["status"], "answer", question)

    def test_evidence_gaps_section_does_not_trigger_the_refusal_check(self):
        answer = "### Dilema\nInformar o no [1].\n### Vacíos de evidencia\nLas fuentes no mencionan sanciones."
        self.assertEqual(run(tokens=(answer,))[1]["status"], "answer")

    def test_no_evidence_skips_the_model(self):
        _, final, answer = run(hits=(WEAK,))
        self.assertEqual(final["status"], "no_evidence")
        answer.assert_not_called()

    def test_out_of_domain(self):
        _, final, answer = run(text="Dame una receta de paella valenciana", intent="fuera_de_dominio", hits=())
        self.assertEqual(final["status"], "out_of_domain")
        answer.assert_not_called()

    def test_ambiguous(self):
        self.assertEqual(run(text="ayúdame con algo por favor", intent="ambigua", hits=())[1]["status"], "ambiguous")

    def test_strong_evidence_overrides_a_wrong_classifier(self):
        self.assertEqual(run(intent="fuera_de_dominio")[1]["status"], "answer")

    def test_greeting_and_farewell_never_call_the_model(self):
        for text, status in (("Hola", "greeting"), ("Olá, bom dia", "greeting"), ("Adiós", "farewell"), ("Goodbye", "farewell")):
            with patch("chat.pipeline.route") as router:
                final = list(pipeline.run(text, "A", [], "es"))[-1]
            self.assertEqual(final["status"], status, text)
            router.assert_not_called()

    def test_about_the_assistant(self):
        self.assertEqual(run(text="¿Qué puedes hacer exactamente tú?", intent="sobre_asistente")[1]["status"], "about")

    def test_system_failure_emits_error_event(self):
        with patch("chat.pipeline.route", side_effect=ConnectionError):
            events = list(pipeline.run("Una consulta cualquiera sobre periodismo", "A", [], "es"))
        self.assertEqual(events[-1]["type"], "error")

    def test_invalid_router_json_falls_back_to_query(self):
        client = MagicMock()
        client.chat.return_value = {"message": {"content": "no es json"}}
        with patch("chat.pipeline._client", return_value=client):
            self.assertEqual(pipeline.route("hola mundo", []), ("consulta", "hola mundo", ""))

    def test_routing_combines_both_readings(self):
        history = [pipeline.Turn("user", "x")]
        table = [
            (("consulta", "a", "a"), ("fuera_de_dominio", "b", "b"), ("consulta", "b", "b")),  # el historial confunde
            (("fuera_de_dominio", "a", "a"), ("consulta", "b", "b"), ("consulta", "b", "b")),  # solo se entiende con contexto
            (("ambigua", "a", "a"), ("consulta", "b", "b"), ("consulta", "b", "b")),  # «¿y sus consecuencias?»
            (("fuera_de_dominio", "a", "a"), ("fuera_de_dominio", "b", "b"), ("fuera_de_dominio", "b", "b")),
            (("ambigua", "a", "a"), ("ambigua", "b", "b"), ("ambigua", "b", "b")),
        ]
        for alone, contextual, expected in table:
            with patch("chat.pipeline._classify", side_effect=[alone, contextual]):
                self.assertEqual(pipeline.route("texto", history), expected)

    def test_routing_without_history_uses_a_single_call(self):
        with patch("chat.pipeline._classify", return_value=("consulta", "a", "a")) as classify:
            pipeline.route("texto", [])
        self.assertEqual(classify.call_count, 1)

    def test_history_drops_old_citation_marks(self):
        turns = pipeline.clean_history([pipeline.Turn("assistant", "Dato [3] final")])
        self.assertEqual(turns[0].content, "Dato  final")


def chat(client, message="¿Debo avisar a la audiencia si usé IA?", **extra):
    with patch("chat.views.pipeline.retrieval.search", return_value=[HIT]), \
         patch("chat.pipeline.route", return_value=("consulta", message, message)), \
         patch("chat.pipeline.stream_answer", return_value=iter(["Informar [1]"])):
        response = client.post(reverse("chat:api_chat"), json.dumps({"message": message, "mode": "C", **extra}), content_type="application/json")
        return [json.loads(line) for line in b"".join(response.streaming_content).decode().splitlines()]


class ApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.ana = User.objects.create_user("ana@example.com", "x")
        self.luis = User.objects.create_user("luis@example.com", "x")

    def test_guest_gets_answer_but_nothing_is_stored(self):
        events = chat(self.client)
        self.assertEqual(events[-1]["status"], "answer")
        self.assertEqual(Conversation.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)

    def test_registered_user_conversation_is_saved_with_citations(self):
        self.client.force_login(self.ana)
        events = chat(self.client)
        conversation = Conversation.objects.get(user=self.ana)
        self.assertEqual(events[0]["id"], str(conversation.id))
        self.assertEqual(conversation.messages.count(), 2)
        answer = conversation.messages.get(role="assistant")
        self.assertEqual(answer.citations.get().page, 4)
        self.assertEqual(events[-1]["message_id"], answer.id)

    def test_followup_uses_same_conversation(self):
        self.client.force_login(self.ana)
        first = chat(self.client)[0]["id"]
        chat(self.client, message="¿Y qué consecuencias tiene eso?", conversation_id=first)
        self.assertEqual(Conversation.objects.count(), 1)
        self.assertEqual(Message.objects.count(), 4)

    def test_failed_first_message_leaves_no_trace(self):
        self.client.force_login(self.ana)
        with patch("chat.pipeline.route", side_effect=ConnectionError):
            response = self.client.post(reverse("chat:api_chat"), json.dumps({"message": "Consulta de prueba sobre ética"}), content_type="application/json")
            last = json.loads(b"".join(response.streaming_content).decode().splitlines()[-1])
        self.assertEqual(last["type"], "error")
        self.assertTrue(last["conversation_removed"])
        self.assertEqual(Conversation.objects.count(), 0)

    def _other_conversation(self):
        return Conversation.objects.create(user=self.luis, title="Privada")

    def test_user_cannot_see_or_touch_another_users_conversation(self):
        conversation = self._other_conversation()
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse("chat:conversation", args=[conversation.id])).status_code, 404)
        self.assertEqual(self.client.post(reverse("chat:delete", args=[conversation.id])).status_code, 404)
        self.assertEqual(self.client.get(reverse("chat:export", args=[conversation.id])).status_code, 404)
        response = self.client.post(reverse("chat:api_chat"), json.dumps({"message": "hola qué tal", "conversation_id": str(conversation.id)}), content_type="application/json")
        self.assertEqual(response.status_code, 404)
        self.assertTrue(Conversation.objects.filter(pk=conversation.pk).exists())

    def test_guest_is_redirected_from_history_pages(self):
        conversation = self._other_conversation()
        self.assertEqual(self.client.get(reverse("chat:conversation", args=[conversation.id])).status_code, 302)

    def test_home_shows_no_history_to_guests_and_own_history_to_users(self):
        Conversation.objects.create(user=self.ana, title="Mi conversación")
        self.assertNotContains(self.client.get(reverse("chat:home")), "Mi conversación")
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(reverse("chat:home")), "Mi conversación")
        self.client.force_login(self.luis)
        self.assertNotContains(self.client.get(reverse("chat:home")), "Mi conversación")

    def test_rejects_empty_and_oversized_messages(self):
        for message in ("   ", "x" * 2001):
            response = self.client.post(reverse("chat:api_chat"), json.dumps({"message": message}), content_type="application/json")
            self.assertEqual(response.status_code, 400)

    def test_export_produces_pdf(self):
        self.client.force_login(self.ana)
        chat(self.client)
        conversation = Conversation.objects.get(user=self.ana)
        response = self.client.get(reverse("chat:export", args=[conversation.id]))
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_interface_translations_are_served(self):
        self.assertContains(self.client.get(reverse("chat:home"), headers={"accept-language": "en"}), "What would you like to do today?")
        self.assertContains(self.client.get(reverse("chat:home"), headers={"accept-language": "pt-BR"}), "O que você gostaria de fazer hoje?")
        self.assertContains(self.client.get(reverse("chat:guide"), headers={"accept-language": "pt"}), "Guia de uso")


def _gemini_client():
    client = MagicMock()
    payload = MagicMock(data=base64.b64encode(b"RIFFdata").decode())
    client.interactions.create.return_value = MagicMock(output_image=payload, output_audio=payload)
    return client


@override_settings(GEMINI_API_KEY="clave", GEMINI_DAILY_AUDIO_LIMIT=1, MEDIA_ROOT="/tmp/claude-1000/-home-anthony-Documentos-Chat-IA/caa7948b-67b5-4b40-ac83-b043d0f999e7/scratchpad/test_media")
class GenerationTests(TestCase):
    def setUp(self):
        self.ana = User.objects.create_user("ana@example.com", "x")
        conversation = Conversation.objects.create(user=self.ana, title="t")
        self.message = Message.objects.create(conversation=conversation, role="assistant", content="### Dilema\nInformar **es clave** [1]", status="answer")
        self.client.force_login(self.ana)
        self.url = reverse("chat:generate", args=[self.message.id, "audio"])

    def test_generates_once_and_reuses_the_asset(self):
        gemini = _gemini_client()
        with patch("chat.media.genai.Client", return_value=gemini):
            first = self.client.post(self.url).json()
            second = self.client.post(self.url).json()
        self.assertEqual(first["url"], second["url"])
        self.assertEqual(gemini.interactions.create.call_count, 1)
        self.assertEqual(self.client.get(first["url"]).status_code, 200)

    def test_gemini_receives_only_the_answer_text_and_no_search_tools(self):
        gemini = _gemini_client()
        with patch("chat.media.genai.Client", return_value=gemini):
            self.client.post(self.url)
        kwargs = gemini.interactions.create.call_args.kwargs
        self.assertNotIn("tools", kwargs)
        self.assertEqual(kwargs["input"], "Dilema\nInformar es clave")

    def test_daily_limit(self):
        GeneratedAsset.objects.create(message=self.message, kind="audio", file="x.wav", model_name="m")
        other = Message.objects.create(conversation=self.message.conversation, role="assistant", content="b [1]", status="answer")
        response = self.client.post(reverse("chat:generate", args=[other.id, "audio"]))
        self.assertEqual((response.status_code, response.json()["error"]), (429, "quota"))

    def test_other_users_and_guests_cannot_generate_or_download(self):
        asset = GeneratedAsset.objects.create(message=self.message, kind="audio", file="x.wav", model_name="m")
        self.client.force_login(User.objects.create_user("luis@example.com", "x"))
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertEqual(self.client.get(reverse("chat:asset", args=[asset.id])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(self.url).status_code, 302)

    def test_limitations_cannot_be_turned_into_media(self):
        limitation = Message.objects.create(conversation=self.message.conversation, role="assistant", content="sin evidencia", status="no_evidence")
        self.assertEqual(self.client.post(reverse("chat:generate", args=[limitation.id, "image"])).status_code, 400)

    def test_plain_text_strips_markdown_and_citations(self):
        self.assertEqual(media.plain_text("**Hola** [2] _mundo_"), "Hola mundo")

    @override_settings(GEMINI_API_KEY="")
    def test_missing_key_is_reported(self):
        self.assertEqual(self.client.post(self.url).json()["error"], "gemini_missing")
