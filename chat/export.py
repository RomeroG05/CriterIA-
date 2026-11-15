"""Exporta una conversación a PDF con sus fuentes, sin servicios externos."""
import html
import io
from pathlib import Path

import markdown
import pymupdf
from django.utils import timezone

EMBLEM = Path(__file__).resolve().parent.parent / "static" / "img" / "criteria-emblema-hd.png"

CSS = """
* { font-family: sans-serif; }
body { font-size: 10.5pt; line-height: 1.45; color: #142132; }
h1 { font-size: 18pt; color: #0e2742; margin-bottom: 2pt; }
h3 { font-size: 12pt; color: #0e2742; margin: 8pt 0 3pt 0; }
.meta { color: #4e5b6d; font-size: 9pt; }
.who { font-weight: bold; color: #7d5b29; margin-top: 14pt; font-size: 9.5pt; }
.src { font-size: 9pt; color: #4e5b6d; }
.brand { margin: 0 0 8pt 0; }
"""


def _md(text: str) -> str:
    return markdown.markdown(html.escape(text, quote=False), extensions=["sane_lists"])


def conversation_html(conversation, labels: dict) -> str:
    parts = [
        f"<h1>{html.escape(conversation.title)}</h1>",
        f"<p class='meta'>CriterIA · {timezone.localtime().strftime('%Y-%m-%d %H:%M')}</p>",
    ]
    for message in conversation.messages.prefetch_related("citations"):
        who = labels["you"] if message.role == "user" else "CriterIA"
        parts.append(f"<p class='who'>{who}</p>{_md(message.content)}")
        for citation in message.citations.all():
            parts.append(f"<p class='src'>[{citation.number}] {html.escape(citation.title)}, p. {citation.page}</p>")
    return "".join(parts)


def conversation_pdf(conversation, labels: dict) -> bytes:
    body, archive = conversation_html(conversation, labels), None
    if EMBLEM.exists():
        body = f'<p class="brand"><img src="{EMBLEM.name}" width="36"/></p>' + body
        archive = pymupdf.Archive(str(EMBLEM.parent))
    story = pymupdf.Story(html=body, user_css=CSS, archive=archive)
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    page = pymupdf.paper_rect("a4")
    area = page + (50, 50, -50, -50)
    more = 1
    while more:
        device = writer.begin_page(page)
        more, _ = story.place(area)
        story.draw(device)
        writer.end_page()
    writer.close()
    return buffer.getvalue()
