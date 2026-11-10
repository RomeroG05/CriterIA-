"""Embeddings locales. Se calculan en CPU para dejar la GPU libre al modelo de chat."""
import numpy as np
import ollama
from django.conf import settings

BATCH = 32


def embed_texts(texts: list[str]) -> np.ndarray:
    client = ollama.Client(host=settings.OLLAMA_HOST, timeout=settings.OLLAMA_TIMEOUT)
    vectors = []
    for start in range(0, len(texts), BATCH):
        response = client.embed(
            model=settings.OLLAMA_EMBED_MODEL,
            input=texts[start : start + BATCH],
            options={"num_gpu": 0},
            keep_alive="30m",
        )
        vectors.extend(response["embeddings"])
    matrix = np.asarray(vectors, dtype=np.float32)
    return matrix / np.clip(np.linalg.norm(matrix, axis=1, keepdims=True), 1e-9, None)
