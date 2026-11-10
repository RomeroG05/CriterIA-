from pathlib import Path

import pymupdf
from django.conf import settings
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.utils.translation import get_language
from django.views.decorators.cache import cache_control
from django.views.decorators.http import require_GET

from . import citations
from .models import Chunk, Document

DPI = 130


def _ready(pk) -> Document:
    return get_object_or_404(Document, pk=pk, status=Document.Status.READY)


@require_GET
def library(request):
    documents = Document.objects.filter(status=Document.Status.READY)
    return render(request, "corpus/library.html", {"documents": documents})


@require_GET
def document_file(request, pk):
    document = _ready(pk)
    return FileResponse(document.file.open("rb"), content_type="application/pdf",
                        filename=f"{document.title[:80]}.pdf")


@require_GET
@cache_control(max_age=86400, private=True)
def page_image(request, pk, page):
    document = _ready(pk)
    if not 1 <= page <= document.pages:
        raise Http404
    cached = Path(settings.MEDIA_ROOT) / "cache" / "pages" / document.sha256[:16] / f"{page}.jpg"
    if not cached.exists():
        cached.parent.mkdir(parents=True, exist_ok=True)
        with pymupdf.open(document.file.path) as pdf:
            pix = pdf[page - 1].get_pixmap(dpi=DPI, alpha=False)
            cached.write_bytes(pix.tobytes("jpeg", jpg_quality=82))
    return FileResponse(cached.open("rb"), content_type="image/jpeg")


@require_GET
def source_detail(request, chunk_id):
    chunk = get_object_or_404(Chunk.objects.select_related("document"), pk=chunk_id)
    document = chunk.document
    return JsonResponse(
        {
            "chunk_id": chunk.id,
            "document_id": document.id,
            "title": document.title,
            "authors": document.authors,
            "year": document.year,
            "publisher": document.publisher,
            "pages": document.pages,
            "page": chunk.page,
            "text": chunk.text,
            "bboxes": chunk.bboxes,
            "sha256": document.sha256,
            "citation": citations.build(document, chunk.page, (get_language() or "es")[:2]),
        }
    )
