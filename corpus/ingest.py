"""Convierte un PDF en fragmentos con página, rectángulos para resaltar y embedding."""
import logging
import re
import subprocess
import unicodedata
from collections import Counter
from dataclasses import dataclass

import pymupdf
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .embeddings import embed_texts
from .models import Chunk, Document

log = logging.getLogger(__name__)

FLAGS = pymupdf.TEXTFLAGS_WORDS | pymupdf.TEXT_DEHYPHENATE
MIN_PAGE_WORDS = 15
MIN_CHUNK_WORDS = 12
MARGIN = 0.09  # fracción superior e inferior donde viven encabezados y pies de página
_ACCENTS = {"´": "́", "`": "̀", "¨": "̈", "˜": "̃"}
_ACCENT_RE = re.compile(r"([´`¨˜])([a-zA-Zı])")  # acentos sueltos típicos de PDFs de LaTeX
_LIGATURES = str.maketrans({"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st", "\u2028": " ", "\u2029": " "})
_HYPHENS = ("-", "\u00ad", "\u2010")
_JUNK_TITLE = re.compile(r"microsoft word|\.docx?$|^documento\d*$|^untitled|^sin t[ií]tulo|_", re.I)


class IngestError(Exception):
    pass


@dataclass
class Word:
    x0: float
    y0: float
    x1: float
    y1: float
    text: str
    line: tuple[int, int]


@dataclass
class Piece:
    page: int
    text: str
    bboxes: list[list[float]]


def fix_word(word: str) -> str:
    def compose(match):
        base = "i" if match[2] == "ı" else match[2]
        return base + _ACCENTS[match[1]]

    return unicodedata.normalize("NFC", _ACCENT_RE.sub(compose, word.translate(_LIGATURES)))


def join_hyphenated(words: list[Word]) -> list[Word]:
    """Une palabras cortadas al final de línea («AU-» + «TORIDAD»). La segunda queda vacía
    pero conserva su posición, para que el resaltado siga cubriendo ambas líneas."""
    for first, second in zip(words, words[1:]):
        text, nxt = first.text, second.text
        if len(text) > 2 and text[-1] in _HYPHENS and text[-2].isalpha() and nxt[:1].isalpha() and first.line != second.line:
            if nxt[0].islower() or (text[-2].isupper() and nxt[0].isupper()):
                first.text, second.text = text[:-1] + nxt, ""
    return words


def pick_title(metadata_title: str, first_page: str, filename: str) -> str:
    """El título de los metadatos del PDF suele ser basura («Microsoft Word - Documento2»).
    Solo se usa si parece real y coincide con el texto de la primera página."""
    words = re.findall(r"\w{4,}", metadata_title.lower())
    page = first_page.lower()
    if metadata_title and not _JUNK_TITLE.search(metadata_title) and words and sum(w in page for w in words) >= len(words) / 2:
        return metadata_title
    name = re.sub(r"^\d+[\s_-]+", "", filename.rsplit("/", 1)[-1].removesuffix(".pdf"))
    name = re.sub(r"[_-]+", " ", name).strip()
    return name[:1].upper() + name[1:]


def ocr_languages() -> str:
    """Idiomas de OCR instalados entre los que usa el proyecto (spa, por, eng)."""
    try:
        output = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    installed = set(output.split())
    return "+".join(code for code in ("spa", "por", "eng") if code in installed)


def _read_words(page: pymupdf.Page, languages: str) -> list[Word]:
    raw = page.get_text("words", flags=FLAGS)
    if len(raw) < MIN_PAGE_WORDS and languages and page.get_images():
        textpage = page.get_textpage_ocr(language=languages, dpi=200, full=True)
        recognized = page.get_text("words", textpage=textpage, flags=FLAGS)
        if len(recognized) > 2 * len(raw) + 10:  # solo si la página era realmente una imagen
            raw = recognized
    return [Word(w[0], w[1], w[2], w[3], fix_word(w[4]), (w[5], w[6])) for w in raw]


def _lines(words: list[Word]) -> dict[tuple[int, int], list[Word]]:
    grouped: dict[tuple[int, int], list[Word]] = {}
    for word in words:
        grouped.setdefault(word.line, []).append(word)
    return grouped


def _margin_key(line: list[Word]) -> str:
    return re.sub(r"[\d\W_]+", " ", " ".join(w.text for w in line)).strip().lower()


def strip_repeated_margins(pages: list[tuple[list[Word], float]]) -> list[list[Word]]:
    """Quita encabezados y pies que se repiten, además de los números de página."""

    def in_margin(line, height):
        return max(w.y1 for w in line) < MARGIN * height or min(w.y0 for w in line) > (1 - MARGIN) * height

    counts: Counter[str] = Counter()
    for words, height in pages:
        counts.update({_margin_key(l) for l in _lines(words).values() if in_margin(l, height)})
    limit = max(3, int(0.3 * len(pages)))
    repeated = {key for key, seen in counts.items() if seen >= limit} | {""}

    cleaned = []
    for words, height in pages:
        kept: list[Word] = []
        for line in _lines(words).values():
            if not (in_margin(line, height) and _margin_key(line) in repeated):
                kept.extend(line)
        cleaned.append(kept)
    return cleaned


def split_words(words: list[Word], size: int, overlap: int) -> list[list[Word]]:
    """Ventana deslizante que intenta cortar en un final de oración."""
    chunks, start, total = [], 0, len(words)
    while start < total:
        end = min(start + size, total)
        if end < total:
            for candidate in range(end, max(start + size - 40, start + 1), -1):
                if words[candidate - 1].text.endswith((".", "?", "!", ":", ";")):
                    end = candidate
                    break
        chunks.append(words[start:end])
        if end >= total:
            break
        start = max(end - overlap, start + 1)
    return chunks


def _bboxes(words: list[Word], width: float, height: float) -> list[list[float]]:
    boxes = []
    for line in _lines(words).values():
        boxes.append(
            [
                round(min(w.x0 for w in line) / width, 4),
                round(min(w.y0 for w in line) / height, 4),
                round(max(w.x1 for w in line) / width, 4),
                round(max(w.y1 for w in line) / height, 4),
            ]
        )
    return boxes


def extract_pieces(path: str) -> tuple[list[Piece], int, str, int]:
    """Devuelve (fragmentos, páginas, título propuesto, páginas sin texto)."""
    languages = ocr_languages()
    pieces: list[Piece] = []
    with pymupdf.open(path) as pdf:
        sizes = [(page.rect.width, page.rect.height) for page in pdf]
        pages = [(_read_words(page, languages), page.rect.height) for page in pdf]
        cleaned = [join_hyphenated(words) for words in strip_repeated_margins(pages)]
        first_page = " ".join(w.text for w in pages[0][0]) if pages else ""
        blank = sum(len(words) < MIN_PAGE_WORDS for words in cleaned)
        for number, words in enumerate(cleaned, start=1):
            width, height = sizes[number - 1]
            for part in split_words(words, settings.CHUNK_WORDS, settings.CHUNK_OVERLAP):
                if len(part) >= MIN_CHUNK_WORDS:
                    pieces.append(Piece(number, " ".join(w.text for w in part if w.text), _bboxes(part, width, height)))
        title = pick_title((pdf.metadata or {}).get("title", "").strip(), first_page, path)
        return pieces, pdf.page_count, title, blank


def index_document(document: Document) -> int:
    """Indexa el documento de forma atómica. Devuelve el número de fragmentos."""
    Document.objects.filter(pk=document.pk).update(status=Document.Status.PROCESSING, error="")
    try:
        pieces, pages, auto_title, blank = extract_pieces(document.file.path)
        if not pieces:
            raise IngestError(
                "No se pudo extraer texto. Si es un PDF escaneado instala OCR en español y portugués: "
                "sudo dnf install tesseract-langpack-spa tesseract-langpack-por"
            )
        if blank:
            log.warning("%s: %d de %d páginas sin texto legible.", document.file.name, blank, pages)
        title = document.title or auto_title
        vectors = embed_texts([f"{title}\n{piece.text}" for piece in pieces])
        with transaction.atomic():
            document.chunks.all().delete()
            Chunk.objects.bulk_create(
                [
                    Chunk(
                        document=document,
                        page=piece.page,
                        order=order,
                        text=piece.text,
                        bboxes=piece.bboxes,
                        embedding=vectors[order].tobytes(),
                    )
                    for order, piece in enumerate(pieces)
                ],
                batch_size=200,
            )
            Document.objects.filter(pk=document.pk).update(
                title=title, pages=pages, status=Document.Status.READY, error="", indexed_at=timezone.now()
            )
    except Exception as exc:
        Document.objects.filter(pk=document.pk).update(status=Document.Status.ERROR, error=str(exc)[:1000])
        raise
    document.refresh_from_db()
    return len(pieces)
