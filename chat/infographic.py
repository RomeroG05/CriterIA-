"""Infografía local de una respuesta: pregunta, respuesta y fuentes, en PNG.

Se genera con PyMuPDF, sin internet ni modelos de imagen: solo muestra lo que la respuesta
ya dice, con sus citas, así que no puede inventar nada.
"""
import html
import re

import markdown
import pymupdf
from django.utils import timezone

WIDTH = 720  # puntos; con ZOOM sale una imagen de 1440 px de ancho
MARGIN = 44
BAND = 74
ZOOM = 2

NAVY = (0x11 / 255, 0x1A / 255, 0x2E / 255)
ORANGE = (1, 0x7A / 255, 0x1A / 255)
PAPER = (1, 1, 1)
SOFT = (0xF3 / 255, 0xF5 / 255, 0xF9 / 255)

LABELS = {
    "es": {"sources": "Fuentes", "page": "p.", "mode": {"A": "Concepto", "B": "Casos", "C": "Dilema ético"},
           "footer": "Respuesta generada solo con los PDF del corpus. Verifica siempre la fuente."},
    "en": {"sources": "Sources", "page": "p.", "mode": {"A": "Concept", "B": "Cases", "C": "Ethical dilemma"},
           "footer": "Answer generated only from the corpus PDFs. Always check the source."},
    "pt": {"sources": "Fontes", "page": "p.", "mode": {"A": "Conceito", "B": "Casos", "C": "Dilema ético"},
           "footer": "Resposta gerada apenas com os PDF do corpus. Verifique sempre a fonte."},
}

CSS = """
* { font-family: sans-serif; }
body { color: #1a2235; font-size: 12.5px; line-height: 1.5; }
h1 { font-size: 21px; color: #111a2e; margin: 0 0 6px 0; line-height: 1.25; }
h3 { font-size: 14.5px; color: #c2500a; margin: 14px 0 4px 0; }
p { margin: 0 0 8px 0; }
ul, ol { margin: 0 0 8px 0; padding-left: 18px; }
li { margin: 0 0 5px 0; }
.cite { color: #c2500a; font-weight: bold; font-size: 10px; }
.src-title { font-size: 13px; font-weight: bold; color: #111a2e; margin: 0 0 6px 0; }
.src { font-size: 11px; color: #4a5468; margin: 0 0 4px 0; }
.num { color: #ff7a1a; font-weight: bold; }
.footer { font-size: 9.5px; color: #7a8396; }
"""


def _answer_html(content: str) -> str:
    body = markdown.markdown(html.escape(content, quote=False), extensions=["sane_lists"])
    return re.sub(r"\[(\d{1,2})\]", r'<span class="cite">[\1]</span>', body)


def _sources_html(citations, labels) -> str:
    if not citations:
        return ""
    rows = "".join(
        f'<p class="src"><span class="num">[{c.number}]</span> {html.escape(c.title)}, {labels["page"]} {c.page}</p>'
        for c in citations
    )
    return f'<p class="src-title">{labels["sources"]}</p>{rows}'


def _fit(page, rect, html_text, css=CSS) -> float:
    """Escribe el HTML en el rectángulo y devuelve la altura que ocupó."""
    spare, _ = page.insert_htmlbox(rect, html_text, css=css, scale_low=1)
    return rect.height - spare


def _measure(html_text: str, width: float, css: str = CSS) -> float:
    scratch = pymupdf.open()
    page = scratch.new_page(width=width + 2 * MARGIN, height=6000)
    return _fit(page, pymupdf.Rect(0, 0, width, 6000), html_text, css)


def compose(width, head_html, citations, language, mode, subtitle="", body_height=0, draw_body=None) -> bytes:
    """Lámina común: cabecera de CriterIA, título, cuerpo opcional, recuadro de fuentes y pie."""
    labels = LABELS.get(language, LABELS["es"])
    inner = width - 2 * MARGIN
    sources_html = _sources_html(citations, labels)
    head_height = _measure(head_html, inner)
    sources_height = _measure(sources_html, inner - 28) + 26 if sources_html else 0
    body_space = body_height + 18 if draw_body else 0
    height = BAND + 30 + head_height + 18 + body_space + sources_height + 56

    document = pymupdf.open()
    page = document.new_page(width=width, height=height)
    page.draw_rect(page.rect, color=None, fill=PAPER)
    page.draw_rect(pymupdf.Rect(0, 0, width, BAND), color=None, fill=NAVY)
    page.draw_rect(pymupdf.Rect(0, BAND, width, BAND + 4), color=None, fill=ORANGE)
    page.insert_htmlbox(
        pymupdf.Rect(MARGIN, 20, width / 2, BAND - 10),
        '<p style="font-size:24px;font-weight:bold;color:#ffffff;margin:0">Criter<span style="color:#ff7a1a">IA</span></p>',
        css=CSS,
    )
    tag = subtitle or labels["mode"].get(mode, "")
    page.insert_htmlbox(
        pymupdf.Rect(width / 2, 30, width - MARGIN, BAND - 10),
        f'<p style="text-align:right;font-size:12px;color:#a2acc2;margin:0">{html.escape(tag)} · {language.upper()}</p>',
        css=CSS,
    )

    top = BAND + 30
    _fit(page, pymupdf.Rect(MARGIN, top, width - MARGIN, top + head_height + 4), head_html)
    top += head_height + 18
    if draw_body:
        draw_body(page, pymupdf.Rect(MARGIN, top, width - MARGIN, top + body_height))
        top += body_space
    if sources_html:
        box = pymupdf.Rect(MARGIN, top, width - MARGIN, top + sources_height)
        page.draw_rect(box, color=None, fill=SOFT, radius=0.04)
        page.draw_rect(pymupdf.Rect(box.x0, box.y0, box.x0 + 4, box.y1), color=None, fill=ORANGE)
        _fit(page, pymupdf.Rect(box.x0 + 18, box.y0 + 13, box.x1 - 10, box.y1), sources_html)

    footer = f'{html.escape(labels["footer"])} · {timezone.localdate().isoformat()}'
    page.insert_htmlbox(pymupdf.Rect(MARGIN, height - 36, width - MARGIN, height - 10), f'<p class="footer">{footer}</p>', css=CSS)
    return page.get_pixmap(matrix=pymupdf.Matrix(ZOOM, ZOOM), alpha=False).tobytes("png")


def render(question: str, content: str, citations, language: str, mode: str) -> bytes:
    head = f"<h1>{html.escape(question)}</h1>{_answer_html(content)}"
    return compose(WIDTH, head, citations, language, mode)


def question_for(message) -> str:
    return (
        message.conversation.messages.filter(role="user", created_at__lte=message.created_at)
        .order_by("-created_at", "-id")
        .values_list("content", flat=True)
        .first()
        or ""
    )


def render_message(message) -> bytes:
    """Infografía de una respuesta guardada, con la pregunta que la originó."""
    return render(question_for(message), message.content, list(message.citations.all()), message.language, message.mode)
