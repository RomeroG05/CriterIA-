"""Búsqueda híbrida sobre los PDF: semántica (embeddings) + léxica (BM25), fusionadas con RRF."""
import math
import re
import threading
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
from django.conf import settings
from django.db.models import Count, Max

from .embeddings import embed_texts
from .models import Chunk

CANDIDATES = 30
RRF_K = 60
MAX_PER_PAGE = 2
K1, B = 1.5, 0.75
STOPWORDS = set(
    """a al algo como con contra cual cuando de del desde donde e el ella ellas ellos en entre era es esa ese eso esta
    este esto fue ha hay la las le les lo los mas me mi muy no nos o para pero por que quien se sea segun ser si sin
    sobre son su sus tambien te tiene un una uno unos y ya
    an and are as at be been but by can do does for from had has have how if in into is it its of on or should that the
    their them they this to was were what when where which who why will with would you your
    ao aos as com da das de dela do dos e ela em entre esta foi ha isso mais mas na nas nao no nos num numa o os ou para
    pela pelo por qual quando que se sem ser seu sua sobre tem um uma uns voce""".split()
)


def tokenize(text: str) -> list[str]:
    plain = "".join(c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c))
    return [t for t in re.findall(r"[a-z0-9]{3,}", plain) if t not in STOPWORDS]


@dataclass
class Hit:
    chunk_id: int
    document_id: int
    page: int
    title: str
    text: str
    similarity: float


class _Index:
    def __init__(self, rows):
        self.ids = np.array([row[0] for row in rows], dtype=np.int64)
        self.matrix = np.vstack([np.frombuffer(row[1], dtype=np.float32) for row in rows])
        tokens = [tokenize(row[2]) for row in rows]
        self.lengths = np.array([len(t) for t in tokens], dtype=np.float32)
        self.average = float(self.lengths.mean()) or 1.0
        postings = defaultdict(list)
        for position, doc_tokens in enumerate(tokens):
            for term, freq in Counter(doc_tokens).items():
                postings[term].append((position, freq))
        self.postings = {
            term: (np.array([p for p, _ in items]), np.array([f for _, f in items], dtype=np.float32))
            for term, items in postings.items()
        }

    def bm25(self, query: str) -> np.ndarray:
        scores = np.zeros(len(self.ids), dtype=np.float32)
        total = len(self.ids)
        for term in set(tokenize(query)):
            if term not in self.postings:
                continue
            positions, freqs = self.postings[term]
            idf = math.log(1 + (total - len(positions) + 0.5) / (len(positions) + 0.5))
            norm = K1 * (1 - B + B * self.lengths[positions] / self.average)
            scores[positions] += idf * freqs * (K1 + 1) / (freqs + norm)
        return scores


_lock = threading.Lock()
_cached: tuple[tuple, _Index | None] | None = None


def _signature() -> tuple:
    state = Chunk.objects.aggregate(total=Count("id"), last=Max("id"))
    return state["total"], state["last"]


def get_index() -> _Index | None:
    """Índice en memoria; se reconstruye solo cuando cambian los fragmentos."""
    global _cached
    signature = _signature()
    with _lock:
        if _cached is None or _cached[0] != signature:
            rows = list(Chunk.objects.values_list("id", "embedding", "text"))
            _cached = (signature, _Index(rows) if rows else None)
        return _cached[1]


def _ranks(scores: np.ndarray, only_positive: bool = False) -> dict[int, int]:
    order = np.argsort(-scores)[:CANDIDATES]
    if only_positive:
        order = order[scores[order] > 0]
    return {int(position): rank for rank, position in enumerate(order)}


def search(query: str | list[str], top_k: int | None = None) -> list[Hit]:
    """Devuelve fragmentos ordenados por relevancia, con su similitud semántica real.

    Acepta varias formulaciones (p. ej. la pregunta original y su traducción al español)."""
    queries = [query] if isinstance(query, str) else [q for q in query if q.strip()]
    index = get_index()
    if index is None:
        return []
    top_k = top_k or settings.RAG_TOP_K
    dense = (index.matrix @ embed_texts(queries).T).max(axis=1)
    lexical = np.max([index.bm25(q) for q in queries], axis=0)
    fused: dict[int, float] = defaultdict(float)
    for ranks in (_ranks(dense), _ranks(lexical, only_positive=True)):
        for position, rank in ranks.items():
            fused[position] += 1 / (RRF_K + rank)

    ordered = sorted(fused, key=fused.get, reverse=True)
    ordered = [p for p in ordered if dense[p] >= settings.RAG_KEEP_SIMILARITY]
    chunks = Chunk.objects.select_related("document").in_bulk([int(index.ids[p]) for p in ordered])

    hits: list[Hit] = []
    per_page: Counter = Counter()
    for position in ordered:
        chunk = chunks.get(int(index.ids[position]))
        if chunk is None or per_page[(chunk.document_id, chunk.page)] >= MAX_PER_PAGE:
            continue
        per_page[(chunk.document_id, chunk.page)] += 1
        hits.append(
            Hit(chunk.id, chunk.document_id, chunk.page, chunk.document.title, chunk.text, float(dense[position]))
        )
        if len(hits) == top_k:
            break
    return hits


def best_similarity(hits: list[Hit]) -> float:
    return max((hit.similarity for hit in hits), default=0.0)
