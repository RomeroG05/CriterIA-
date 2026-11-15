import os
import json
from unittest.mock import MagicMock, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from corpus.retrieval import Hit

from . import infographic, language, media, pipeline, prompts, speech, visuals
from .models import Citation, Conversation, GeneratedAsset, Message

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


@override_settings(ALLOWED_HOSTS=["testserver"])
class VoiceTests(TestCase):
    def setUp(self):
        cache.clear()

    def _post(self, exception=None, result=("hola", "es")):
        with patch("chat.views.speech.transcribe", side_effect=exception, return_value=result):
            return self.client.post(reverse("chat:transcribe"), {"audio": SimpleUploadedFile("voz.webm", b"x" * 100)})

    def test_transcription_ok(self):
        self.assertEqual(self._post().json(), {"text": "hola", "language": "es"})

    def test_each_problem_has_its_own_error(self):
        for exception, code in ((speech.SilentAudio, "silent"), (speech.TooShort, "too_short"), (speech.NoSpeech, "no_speech")):
            response = self._post(exception)
            self.assertEqual((response.status_code, response.json()["error"]), (422, code))

    def test_silent_and_short_audio_are_detected_before_whisper(self):
        import numpy as np

        for audio, exception in ((np.zeros(16000 * 2, dtype=np.float32), speech.SilentAudio),
                                 (np.full(4000, 0.5, dtype=np.float32), speech.TooShort)):
            with patch("faster_whisper.audio.decode_audio", return_value=audio), patch("chat.speech._get_model") as model:
                with self.assertRaises(exception):
                    speech.transcribe("x.webm")
            model.assert_not_called()

    def test_falls_back_without_voice_detection_when_it_drops_everything(self):
        import numpy as np

        model = MagicMock()
        empty, spoken = MagicMock(language="es"), MagicMock(language="es")
        model.transcribe.side_effect = [(iter([]), empty), (iter([MagicMock(text=" Hola mundo ")]), spoken)]
        with patch("faster_whisper.audio.decode_audio", return_value=np.full(32000, 0.05, dtype=np.float32)), \
             patch("chat.speech._get_model", return_value=model):
            self.assertEqual(speech.transcribe("x.webm"), ("Hola mundo", "es"))
        self.assertEqual([c.kwargs["vad_filter"] for c in model.transcribe.call_args_list], [True, False])


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

    def test_short_questions_with_exclusive_marks(self):
        for text, expected in (("É ético usar IA para automatizar comunicados de imprensa?", "pt"),
                               ("Como os meios estão usando a inteligência artificial?", "pt"),
                               ("O que você acha da transparência?", "pt"),
                               ("¿Es ético usar IA para automatizar gacetillas y refritos?", "es"),
                               ("¿Qué es la desinformación?", "es"),
                               ("Trato inhumano a fuentes anheladas", "es"),
                               ("Is it ethical to use AI to automate press releases?", "en")):
            self.assertEqual(language.detect(text, "es"), expected, text)

    def test_dominant_language_of_mixed_text(self):
        mixed = "Definição. El uso de la inteligencia artificial para automatizar comunicados se considera una práctica necesaria que libera tiempo."
        self.assertEqual(language.dominant(mixed), "es")

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
                     "### Definición\nEl documento no define ni describe el concepto de periodismo de soluciones [1].",
                     "### Definição\nO texto não descreve o jornalismo de soluções [1].",
                     "### Definition\nThe document does not define solutions journalism [1].",
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

    def test_answer_is_written_without_history(self):
        client = MagicMock()
        client.chat.return_value = iter([{"message": {"content": "Hola [1]"}}])
        with patch("chat.pipeline._client", return_value=client):
            list(pipeline.stream_answer("A", "pt", "Pergunta?", [HIT]))
        messages = client.chat.call_args.kwargs["messages"]
        self.assertEqual([m["role"] for m in messages], ["system", "user"])
        self.assertIn("Portuguese", messages[-1]["content"].splitlines()[-1])
        self.assertEqual(client.chat.call_args.kwargs["options"]["seed"], 42)

    def test_answer_in_wrong_language_is_translated(self):
        spanish = "### Definição\nEl uso de la inteligencia artificial para automatizar comunicados se considera una práctica necesaria [1]."
        portuguese = "### Definição\nO uso da inteligência artificial para automatizar comunicados é considerado uma prática necessária [1]."
        with patch("chat.pipeline.translate_answer", return_value=portuguese) as translate:
            events, final, _ = run(text="É ético usar IA para automatizar comunicados de imprensa?", tokens=(spanish,))
        translate.assert_called_once()
        self.assertEqual(final["content"], portuguese)
        self.assertIn({"type": "status", "stage": "translating"}, events)

    def test_answer_in_right_language_is_not_translated(self):
        with patch("chat.pipeline.translate_answer") as translate:
            final = run(tokens=("### Definición\nLa transparencia editorial exige informar a la audiencia sobre el uso de herramientas [1].",))[1]
        translate.assert_not_called()
        self.assertEqual(final["status"], "answer")

    def test_failed_translation_keeps_the_grounded_answer(self):
        spanish = "### Definição\nEl uso de la inteligencia artificial para automatizar comunicados se considera una práctica necesaria [1]."
        with patch("chat.pipeline.translate_answer", return_value="tradução sem citações"):
            final = run(text="É ético usar IA para automatizar comunicados de imprensa?", tokens=(spanish,))[1]
        self.assertEqual((final["status"], final["content"]), ("answer", spanish))

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


class FakeVoice:
    def __init__(self):
        self.texts = []

    def synthesize_wav(self, text, wav_file):
        self.texts.append(text)
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(22050)
        wav_file.writeframes(b"\0\0" * 100)


@override_settings(MEDIA_ROOT="/tmp/claude-1000/-home-anthony-Documentos-Chat-IA/caa7948b-67b5-4b40-ac83-b043d0f999e7/scratchpad/test_media")
class GenerationTests(TestCase):
    def setUp(self):
        self.ana = User.objects.create_user("ana@example.com", "x")
        conversation = Conversation.objects.create(user=self.ana, title="t")
        self.message = Message.objects.create(conversation=conversation, role="assistant", content="### Dilema\nInformar **es clave** [1]", status="answer", language="pt")
        self.client.force_login(self.ana)
        self.audio_url = reverse("chat:generate", args=[self.message.id, "audio"])
        self.image_url = reverse("chat:generate", args=[self.message.id, "image"])
        self.voice = FakeVoice()

    def _voice(self, language):
        self.voice_language = language
        return "voz-prueba", self.voice

    def test_audio_is_local_generated_once_and_reused(self):
        with patch("chat.media._voice", side_effect=self._voice):
            first = self.client.post(self.audio_url).json()
            second = self.client.post(self.audio_url).json()
        self.assertEqual(first["url"], second["url"])
        self.assertEqual(self.voice.texts, ["Dilema\nInformar es clave"])
        response = self.client.get(first["url"])
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "audio/wav"))

    def test_audio_uses_the_voice_of_the_answer_language(self):
        with patch("chat.media._voice", side_effect=self._voice):
            self.client.post(self.audio_url)
        self.assertEqual(self.voice_language, "pt")

    def test_missing_voice_is_reported(self):
        with override_settings(PIPER_VOICES_DIR="/tmp/claude-1000/-home-anthony-Documentos-Chat-IA/caa7948b-67b5-4b40-ac83-b043d0f999e7/scratchpad/sin_voces"):
            from chat import media as media_module
            media_module._voices.clear()
            self.assertEqual(self.client.post(self.audio_url).json()["error"], "voice_missing")

    def test_owner_can_delete_an_asset_and_generate_it_again(self):
        data = self.client.post(self.image_url).json()
        asset = GeneratedAsset.objects.get(message=self.message, kind="image")
        path = asset.file.path
        self.assertTrue(os.path.exists(path))
        self.assertEqual(self.client.post(data["delete_url"]).json(), {"deleted": True})
        self.assertFalse(GeneratedAsset.objects.filter(pk=asset.pk).exists())
        self.assertFalse(os.path.exists(path))
        self.assertEqual(self.client.get(data["url"]).status_code, 404)
        self.assertNotEqual(self.client.post(self.image_url).json()["url"], data["url"])

    def test_visual_kinds_extract_from_the_answer_and_render_locally(self):
        structures = {
            "concept_map": {"central": "Transparencia", "branches": [{"concept": "Informar", "details": ["a"]}, {"concept": "Explicar", "details": ["b"]}]},
            "ishikawa": {"effect": "Publicar sin revisión", "categories": [{"name": "Calidad", "causes": ["x"]}, {"name": "Control", "causes": ["y"]}]},
            "comparison": {"criteria": ["Riesgo"], "options": [{"name": "Avisar", "values": ["Bajo"]}, {"name": "Ocultar", "values": ["Alto"]}]},
        }
        for kind, data in structures.items():
            with patch("chat.visuals.extract", return_value=visuals.clean(kind, data)) as extract:
                response = self.client.post(reverse("chat:generate", args=[self.message.id, kind]))
            self.assertEqual(response.status_code, 200, kind)
            self.assertEqual(extract.call_args.args, (kind, self.message.content, "pt"))
            self.assertEqual(self.client.get(response.json()["url"])["Content-Type"], "image/png")
        self.assertEqual(self.message.assets.count(), 3)

    def test_thin_structures_are_rejected_instead_of_drawn(self):
        for kind, data in (("concept_map", {"central": "X", "branches": [{"concept": "solo una", "details": []}]}),
                           ("ishikawa", {"effect": "", "categories": []}),
                           ("comparison", {"criteria": ["a"], "options": [{"name": "una", "values": ["b"]}]})):
            with self.assertRaises(visuals.NotEnoughContent):
                visuals.clean(kind, data)
        with patch("chat.visuals.extract", side_effect=visuals.NotEnoughContent):
            response = self.client.post(reverse("chat:generate", args=[self.message.id, "ishikawa"]))
        self.assertEqual((response.status_code, response.json()["error"]), (422, "not_enough"))

    def test_unknown_visual_kind_is_rejected(self):
        self.assertEqual(self.client.post(reverse("chat:generate", args=[self.message.id, "pizza"])).status_code, 400)

    def test_others_cannot_delete_assets(self):
        asset = GeneratedAsset.objects.create(message=self.message, kind="audio", file="x.wav", model_name="m")
        url = reverse("chat:asset_delete", args=[asset.id])
        self.client.force_login(User.objects.create_user("luis@example.com", "x"))
        self.assertEqual(self.client.post(url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertTrue(GeneratedAsset.objects.filter(pk=asset.pk).exists())

    def test_other_users_and_guests_cannot_generate_or_download(self):
        asset = GeneratedAsset.objects.create(message=self.message, kind="audio", file="x.wav", model_name="m")
        self.client.force_login(User.objects.create_user("luis@example.com", "x"))
        self.assertEqual(self.client.post(self.audio_url).status_code, 404)
        self.assertEqual(self.client.get(reverse("chat:asset", args=[asset.id])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(self.audio_url).status_code, 302)

    def test_limitations_cannot_be_turned_into_media(self):
        limitation = Message.objects.create(conversation=self.message.conversation, role="assistant", content="sin evidencia", status="no_evidence")
        self.assertEqual(self.client.post(reverse("chat:generate", args=[limitation.id, "image"])).status_code, 400)

    def test_plain_text_strips_markdown_and_citations(self):
        self.assertEqual(media.plain_text("**Hola** [2] _mundo_"), "Hola mundo")

    def test_image_is_a_local_infographic(self):
        Message.objects.create(conversation=self.message.conversation, role="user", content="¿Debo avisar del uso de IA?")
        answer = Message.objects.create(conversation=self.message.conversation, role="assistant", status="answer", language="es",
                                        content="### Definición\nHay que informar [1].")
        Citation.objects.create(message=answer, number=1, title="Guía ADEPA", page=4, similarity=0.7, quote="q")
        with patch("chat.infographic.render", wraps=infographic.render) as render:
            url = self.client.post(reverse("chat:generate", args=[answer.id, "image"])).json()["url"]
        question, content, citations, language, mode = render.call_args.args
        self.assertEqual((question, language, [c.page for c in citations]), ("¿Debo avisar del uso de IA?", "es", [4]))
        response = self.client.get(url)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\x89PNG"))

    def test_infographic_has_no_daily_limit(self):
        GeneratedAsset.objects.create(message=self.message, kind="image", file="x.png", model_name="m")
        other = Message.objects.create(conversation=self.message.conversation, role="assistant", content="Dato b [1]", status="answer")
        self.assertEqual(self.client.post(reverse("chat:generate", args=[other.id, "image"])).status_code, 200)




class VoiceModeTests(TestCase):
    """Modo voz: respuesta breve y hablada solo para registrados; el chat escrito no cambia."""

    def setUp(self):
        cache.clear()
        self.ana = User.objects.create_user("ana@example.com", "x")

    def test_written_prompt_keeps_its_format_and_spoken_prompt_is_short_plain_text(self):
        written = prompts.answer_system("A", "es")
        spoken = prompts.answer_system("A", "es", voice=True)
        self.assertIn("Use Markdown", written)
        self.assertIn("### Definición", written)
        self.assertNotIn("Use Markdown", spoken)
        self.assertNotIn("###", spoken)
        self.assertIn("spoken aloud", spoken)
        self.assertIn("square brackets", spoken)  # sigue citando sus fuentes

    def test_spoken_answer_is_shorter(self):
        client = MagicMock()
        client.chat.return_value = iter([{"message": {"content": "Hola [1]"}}])
        with patch("chat.pipeline._client", return_value=client):
            list(pipeline.stream_answer("C", "es", "¿Pregunta?", [HIT], voice=True))
        self.assertEqual(client.chat.call_args.kwargs["options"]["num_predict"], 320)
        self.assertIn("spoken aloud", client.chat.call_args.kwargs["messages"][0]["content"])

    def _voice_flag(self, **extra):
        with patch("chat.views.pipeline.retrieval.search", return_value=[HIT]), \
             patch("chat.pipeline.route", return_value=("consulta", "q", "q")), \
             patch("chat.pipeline.stream_answer", return_value=iter(["Informar [1]"])) as answer:
            response = self.client.post(reverse("chat:api_chat"), json.dumps({"message": "¿Debo avisar si usé IA?", "mode": "A", **extra}),
                                        content_type="application/json")
            b"".join(response.streaming_content)
        return answer.call_args.kwargs["voice"]

    def test_only_registered_users_get_the_spoken_answer(self):
        self.assertFalse(self._voice_flag(voice=True))
        self.client.force_login(self.ana)
        self.assertTrue(self._voice_flag(voice=True))
        self.assertFalse(self._voice_flag())

    def _speak(self, text="Hay que **informar** al público [2].", language="es"):
        return self.client.post(reverse("chat:speak"), json.dumps({"text": text, "language": language}), content_type="application/json")

    def test_speaking_requires_an_account(self):
        self.assertEqual(self._speak().status_code, 403)

    def test_speaks_the_sentence_without_citations_or_markdown_and_stores_nothing(self):
        self.client.force_login(self.ana)
        voice = FakeVoice()
        with patch("chat.media._voice", return_value=("voz", voice)) as pick:
            response = self._speak(language="pt")
        self.assertEqual((response.status_code, response["Content-Type"]), (200, "audio/wav"))
        self.assertTrue(response.content.startswith(b"RIFF"))
        pick.assert_called_with("pt")
        self.assertNotIn("[2]", voice.texts[0])
        self.assertNotIn("**", voice.texts[0])
        self.assertFalse(GeneratedAsset.objects.exists())

    def test_rejects_empty_text_or_unknown_language_and_reports_missing_voices(self):
        self.client.force_login(self.ana)
        self.assertEqual(self._speak(text=" [1] ").status_code, 400)
        self.assertEqual(self._speak(language="fr").status_code, 400)
        with patch("chat.media._voice", side_effect=media.VoiceUnavailable("x")):
            response = self._speak()
        self.assertEqual((response.status_code, response.json()["error"]), (503, "voice_missing"))

    def test_voice_screen_is_only_rendered_for_registered_users(self):
        self.assertNotContains(self.client.get(reverse("chat:home")), 'id="voice-mode"')
        self.client.force_login(self.ana)
        self.assertContains(self.client.get(reverse("chat:home")), 'id="voice-mode"')
