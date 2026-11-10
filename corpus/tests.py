from types import SimpleNamespace

import numpy as np
from django.test import SimpleTestCase, TestCase, override_settings

from . import citations, ingest, retrieval
from .models import Chunk, Document


def _word(text, y0=100.0, line=(0, 0)):
    return ingest.Word(10, y0, 20, y0 + 10, text, line)


class IngestTests(SimpleTestCase):
    def test_fix_word_composes_loose_latex_accents(self):
        self.assertEqual(ingest.fix_word("Introducci´on"), "Introducción")
        self.assertEqual(ingest.fix_word("T´ıtulo"), "Título")
        self.assertEqual(ingest.fix_word("ma˜nana"), "mañana")

    def test_fix_word_expands_ligatures_and_line_separators(self):
        self.assertEqual(ingest.fix_word("artiﬁcial"), "artificial")
        self.assertEqual(ingest.fix_word("cancelada\u2028Si"), "cancelada Si")

    def test_join_hyphenated_across_lines_only(self):
        words = [_word("AU-", line=(0, 0)), _word("TORIDAD", line=(0, 1)), _word("con-", line=(0, 1)), _word("ducción", line=(0, 2)),
                 _word("Art.", line=(0, 2)), _word("1º)–", line=(0, 2)), _word("Quedan", line=(0, 3)), _word("anti-", line=(0, 3)), _word("Trump", line=(0, 4))]
        texts = [w.text for w in ingest.join_hyphenated(words)]
        self.assertEqual(texts, ["AUTORIDAD", "", "conducción", "", "Art.", "1º)–", "Quedan", "anti-", "Trump"])

    def test_pick_title_rejects_junk_metadata(self):
        page = "Estatuto del Periodista Profesional (Ley 12.908) Art. 1"
        self.assertEqual(ingest.pick_title("Microsoft Word - Documento2", page, "corpus/7_trabajo_periodistico.pdf"), "Trabajo periodistico")
        self.assertEqual(ingest.pick_title("access_to_information_model_law", page, "x/1 constitucion.pdf"), "Constitucion")
        self.assertEqual(ingest.pick_title("Estatuto del Periodista Profesional", page, "x/a.pdf"), "Estatuto del Periodista Profesional")
        self.assertEqual(ingest.pick_title("Informe anual de ventas", page, "x/guia-ia.pdf"), "Guia ia")

    def test_split_words_overlaps_and_covers_everything(self):
        words = [_word(f"w{i}") for i in range(500)]
        parts = ingest.split_words(words, 180, 40)
        self.assertGreater(len(parts), 2)
        self.assertEqual(parts[0][0].text, "w0")
        self.assertEqual(parts[-1][-1].text, "w499")
        self.assertIn(parts[0][-1].text, [w.text for w in parts[1]])

    def test_split_words_prefers_sentence_end(self):
        words = [_word(f"w{i}.") if i == 169 else _word(f"w{i}") for i in range(400)]
        self.assertEqual(ingest.split_words(words, 180, 40)[0][-1].text, "w169.")

    def test_repeated_margins_are_removed_but_body_stays(self):
        pages = []
        for number in range(6):
            pages.append(([_word("Revista", 10, (0, 0)), _word("Ética", 10, (0, 0)), _word("cuerpo", 400, (1, 0)),
                           _word(str(number), 780, (2, 0))], 800.0))
        cleaned = ingest.strip_repeated_margins(pages)
        self.assertEqual([[w.text for w in page] for page in cleaned], [["cuerpo"]] * 6)


class SearchTests(TestCase):
    def setUp(self):
        document = Document.objects.create(title="Doc", sha256="a" * 64, status=Document.Status.READY, pages=3)
        vectors = {"deepfake electoral manipulado": [1, 0, 0], "privacidad de las fuentes": [0, 1, 0], "receta": [0, 0, 1]}
        for order, (text, vector) in enumerate(vectors.items()):
            Chunk.objects.create(document=document, page=order + 1, order=order, text=text, bboxes=[],
                                 embedding=np.array(vector, dtype=np.float32).tobytes())

    def _search(self, vector):
        retrieval._cached = None
        target = "corpus.retrieval.embed_texts"
        from unittest.mock import patch

        with patch(target, return_value=np.array([vector], dtype=np.float32)):
            return retrieval.search("deepfake")

    @override_settings(RAG_KEEP_SIMILARITY=0.4)
    def test_returns_only_chunks_above_similarity_floor(self):
        hits = self._search([1, 0, 0])
        self.assertEqual([hit.text for hit in hits], ["deepfake electoral manipulado"])
        self.assertAlmostEqual(hits[0].similarity, 1.0)

    @override_settings(RAG_KEEP_SIMILARITY=0.4)
    def test_several_phrasings_keep_the_best_similarity(self):
        retrieval._cached = None
        from unittest.mock import patch

        with patch("corpus.retrieval.embed_texts", return_value=np.array([[0, 1, 0], [1, 0, 0]], dtype=np.float32)):
            hits = retrieval.search(["consent", "consentimiento"])
        self.assertEqual({hit.text for hit in hits}, {"deepfake electoral manipulado", "privacidad de las fuentes"})

    def test_index_rebuilds_when_chunks_change(self):
        first = retrieval.get_index()
        Chunk.objects.last().delete()
        self.assertIsNot(first, retrieval.get_index())


class CitationTests(SimpleTestCase):
    def _doc(self, **extra):
        data = dict(title="Ética <IA>", authors="Pérez, A.; Lima, B.", year=2024, publisher="UTPL", doi="10.1/x", url="")
        return SimpleNamespace(**{**data, **extra})

    def test_apa_uses_real_metadata_and_escapes_html(self):
        apa = citations.build(self._doc(), 34, "es")["styles"][0]["html"]
        self.assertIn("Pérez, A., &amp; Lima, B. (2024)", apa)
        self.assertIn("&lt;IA&gt;", apa)
        self.assertIn("https://doi.org/10.1/x", apa)

    def test_missing_year_is_not_invented(self):
        built = citations.build(self._doc(year=None), 2, "en")
        self.assertIn("n.d.", built["styles"][0]["html"])
        self.assertTrue(built["incomplete"])

    def test_in_text_includes_page(self):
        self.assertEqual(citations.in_text(self._doc(), 34, "es"), "(Pérez & Lima, 2024, p. 34)")
