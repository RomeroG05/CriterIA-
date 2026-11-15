"""Recursos visuales locales a partir de una respuesta: mapa conceptual, Ishikawa y cuadro comparativo.

El modelo local solo reorganiza el texto de la respuesta (que ya procede de los PDF) en una
estructura JSON; el dibujo se hace con PyMuPDF. No se usa internet.
"""
import html
import json
import re

import ollama
import pymupdf
from django.conf import settings

from . import infographic as base

WIDTH = 960
KINDS = ("concept_map", "ishikawa", "comparison")
TITLES = {
    "concept_map": {"es": "Mapa conceptual", "en": "Concept map", "pt": "Mapa conceitual"},
    "ishikawa": {"es": "Diagrama de Ishikawa", "en": "Ishikawa diagram", "pt": "Diagrama de Ishikawa"},
    "comparison": {"es": "Cuadro comparativo", "en": "Comparison table", "pt": "Quadro comparativo"},
}
LANGUAGE_NAME = {"es": "Spanish", "en": "English", "pt": "Portuguese"}


class NotEnoughContent(Exception):
    """La respuesta no da para ese tipo de diagrama."""


def _array(items, max_items=6):
    return {"type": "array", "items": items, "maxItems": max_items}


SCHEMAS = {
    "concept_map": {
        "type": "object",
        "properties": {
            "central": {"type": "string"},
            "branches": _array({"type": "object", "properties": {"concept": {"type": "string"}, "details": _array({"type": "string"}, 3)},
                                "required": ["concept", "details"]}),
        },
        "required": ["central", "branches"],
    },
    "ishikawa": {
        "type": "object",
        "properties": {
            "effect": {"type": "string"},
            "categories": _array({"type": "object", "properties": {"name": {"type": "string"}, "causes": _array({"type": "string"}, 3)},
                                  "required": ["name", "causes"]}),
        },
        "required": ["effect", "categories"],
    },
    "comparison": {
        "type": "object",
        "properties": {
            "criteria": _array({"type": "string"}, 4),
            "options": _array({"type": "object", "properties": {"name": {"type": "string"}, "values": _array({"type": "string"}, 4)},
                               "required": ["name", "values"]}, 4),
        },
        "required": ["criteria", "options"],
    },
}

TASKS = {
    "concept_map": "Build a concept map: 'central' is the main concept (at most 6 words). Give 3 to 6 'branches', each a key idea "
                   "(at most 5 words) with 1 to 3 'details' (at most 10 words each).",
    "ishikawa": "Build an Ishikawa (cause and effect) diagram: 'effect' is the central problem or dilemma (at most 8 words). "
                "Give 3 to 6 'categories' of causes or factors (at most 3 words each), each with 1 to 3 'causes' (at most 8 words each).",
    "comparison": "Build a comparison table: 2 to 4 'options' (alternatives, positions or cases; name at most 5 words) compared on "
                  "2 to 4 'criteria' (at most 4 words each). Each option has exactly one value per criterion, in the same order "
                  "(at most 14 words). Write '—' when the text says nothing about it.",
}
SYSTEM = ("You turn a text into structured data for a diagram. Use ONLY information stated in the text and never add facts, "
          "names or examples. Write every string in {language}. Return only JSON.")


def extract(kind: str, content: str, language: str) -> dict:
    text = re.sub(r"\[\d{1,2}\]", "", content)
    response = ollama.Client(host=settings.OLLAMA_HOST, timeout=settings.OLLAMA_TIMEOUT).chat(
        model=settings.OLLAMA_CHAT_MODEL,
        think=False,
        format=SCHEMAS[kind],
        keep_alive="30m",
        options={"temperature": 0, "seed": 42, "num_ctx": settings.OLLAMA_NUM_CTX, "num_predict": 900},
        messages=[
            {"role": "system", "content": SYSTEM.format(language=LANGUAGE_NAME.get(language, "Spanish"))},
            {"role": "user", "content": f"{TASKS[kind]}\n\nText:\n{text}"},
        ],
    )
    return clean(kind, json.loads(response["message"]["content"]))


def _strings(values, limit):
    return [v.strip() for v in values if isinstance(v, str) and v.strip()][:limit]


def clean(kind: str, data: dict) -> dict:
    """Recorta y valida la estructura; si no da para el diagrama, lo dice en vez de dibujar algo pobre."""
    if kind == "concept_map":
        branches = [{"concept": b["concept"].strip(), "details": _strings(b.get("details", []), 3)}
                    for b in data.get("branches", []) if str(b.get("concept", "")).strip()][:6]
        if not data.get("central", "").strip() or len(branches) < 2:
            raise NotEnoughContent
        return {"central": data["central"].strip(), "branches": branches}
    if kind == "ishikawa":
        categories = [{"name": c["name"].strip(), "causes": _strings(c.get("causes", []), 3)}
                      for c in data.get("categories", []) if str(c.get("name", "")).strip() and _strings(c.get("causes", []), 3)][:6]
        if not data.get("effect", "").strip() or len(categories) < 2:
            raise NotEnoughContent
        return {"effect": data["effect"].strip(), "categories": categories}
    criteria = _strings(data.get("criteria", []), 4)
    options = [{"name": o["name"].strip(), "values": (_strings(o.get("values", []), len(criteria)) + ["—"] * 4)[:len(criteria)]}
               for o in data.get("options", []) if str(o.get("name", "")).strip()][:4]
    if len(criteria) < 1 or len(options) < 2:
        raise NotEnoughContent
    return {"criteria": criteria, "options": options}


# ---------- Dibujo ----------
def _box(page, rect, fill, border=None, radius=0.08):
    page.draw_rect(rect, color=border, fill=fill, width=1.2, radius=radius)


def _text(page, rect, inner_html, extra_css=""):
    page.insert_htmlbox(rect, inner_html, css=base.CSS + extra_css, scale_low=0)


def _concept_map(page, area, data):
    central, branches = data["central"], data["branches"]
    column, gap = 270, 16
    left, right = branches[::2], branches[1::2]
    heights = {id(b): _branch_height(b, column) for b in branches}

    def column_height(items):
        return sum(heights[id(b)] for b in items) + gap * max(len(items) - 1, 0)

    root = pymupdf.Rect(area.x0 + area.width / 2 - 115, 0, area.x0 + area.width / 2 + 115, 0)
    root_height = base._measure(f'<p class="root">{html.escape(central)}</p>', 200, base.CSS + BRANCH_CSS) + 26
    root.y0 = area.y0 + area.height / 2 - root_height / 2
    root.y1 = root.y0 + root_height
    for items, x0, side in ((left, area.x0, -1), (right, area.x1 - column, 1)):
        y = area.y0 + (area.height - column_height(items)) / 2
        for branch in items:
            rect = pymupdf.Rect(x0, y, x0 + column, y + heights[id(branch)])
            start = pymupdf.Point(root.x0 if side < 0 else root.x1, root.y0 + root.height / 2)
            end = pymupdf.Point(rect.x1 if side < 0 else rect.x0, rect.y0 + rect.height / 2)
            middle = (start.x + end.x) / 2
            page.draw_bezier(start, pymupdf.Point(middle, start.y), pymupdf.Point(middle, end.y), end, color=base.GOLD, width=1.6)
            _box(page, rect, base.SOFT, base.GOLD)
            _text(page, rect + (12, 9, -10, -6), _branch_html(branch), BRANCH_CSS)
            y += heights[id(branch)] + gap
    _box(page, root, base.NAVY, radius=0.18)
    _text(page, root + (14, 12, -14, -8), f'<p class="root">{html.escape(central)}</p>', BRANCH_CSS)


BRANCH_CSS = """
.root { color: #ffffff; font-size: 15px; font-weight: bold; text-align: center; margin: 0; }
.bt { color: #0e2742; font-size: 13px; font-weight: bold; margin: 0 0 4px 0; }
.bd { color: #4e5b6d; font-size: 11px; margin: 0 0 2px 0; }
.lab { color: #1b1404; font-size: 11.5px; font-weight: bold; text-align: center; margin: 0; }
.cause { color: #4e5b6d; font-size: 10px; margin: 0; }
"""


def _branch_html(branch):
    details = "".join(f'<p class="bd">• {html.escape(d)}</p>' for d in branch["details"])
    return f'<p class="bt">{html.escape(branch["concept"])}</p>{details}'


def _branch_height(branch, column):
    return base._measure(_branch_html(branch), column - 22, base.CSS + BRANCH_CSS) + 16


def _concept_map_height(data):
    column_heights = [sum(_branch_height(b, 270) + 16 for b in data["branches"][side::2]) for side in (0, 1)]
    return max(max(column_heights), 120) + 10


def _ishikawa(page, area, data):
    spine_y = area.y0 + area.height / 2
    head_width = 200
    head_height = base._measure(f'<p class="root">{html.escape(data["effect"])}</p>', head_width - 28, base.CSS + BRANCH_CSS) + 24
    head = pymupdf.Rect(area.x1 - head_width, spine_y - head_height / 2, area.x1, spine_y + head_height / 2)
    page.draw_line(pymupdf.Point(area.x0 + 10, spine_y), pymupdf.Point(head.x0, spine_y), color=base.NAVY, width=3)
    _box(page, head, base.NAVY, radius=0.15)
    _text(page, head + (14, 12, -14, -8), f'<p class="root">{html.escape(data["effect"])}</p>', BRANCH_CSS)

    top, bottom = data["categories"][::2], data["categories"][1::2]
    slots = max(len(top), len(bottom))
    usable = head.x0 - 40 - (area.x0 + 30)
    slot = usable / slots
    bone = area.height / 2 - 62
    for items, direction in ((top, -1), (bottom, 1)):
        for index, category in enumerate(items):
            end = pymupdf.Point(area.x0 + 30 + slot * (index + 1), spine_y)
            start = pymupdf.Point(end.x - bone * 0.55, spine_y + direction * bone)
            page.draw_line(start, end, color=base.NAVY, width=1.8)
            label_width = min(slot - 14, 190)
            label_html = f'<p class="lab">{html.escape(category["name"])}</p>'
            label_height = min(base._measure(label_html, label_width - 12, base.CSS + BRANCH_CSS), 30) + 10
            label_y = start.y - label_height - 4 if direction < 0 else start.y + 4
            label = pymupdf.Rect(start.x - label_width / 2, label_y, start.x + label_width / 2, label_y + label_height)
            _box(page, label, base.GOLD, radius=0.3)
            _text(page, label + (6, 5, -6, -2), label_html, BRANCH_CSS)
            causes = category["causes"]
            for position, cause in enumerate(causes):
                t = (position + 1) / (len(causes) + 1)
                point = start + (end - start) * t
                page.draw_line(pymupdf.Point(point.x - 18, point.y), point, color=base.NAVY, width=1)
                box = pymupdf.Rect(point.x - 22 - (slot - 50), point.y - 13, point.x - 22, point.y + 13)
                _text(page, box, f'<p class="cause" style="text-align:right">{html.escape(cause)}</p>', BRANCH_CSS)


TABLE_CSS = """
table { width: 100%; border-collapse: collapse; }
th { background-color: #0e2742; color: #ffffff; font-size: 12px; text-align: left; padding: 7px; border: 1px solid #d7dee7; }
td { font-size: 11px; color: #142132; padding: 7px; border: 1px solid #d7dee7; vertical-align: top; }
td.crit { font-weight: bold; color: #7d5b29; background-color: #f3f5f8; }
"""


def _table_html(data):
    head = "".join(f"<th>{html.escape(o['name'])}</th>" for o in data["options"])
    rows = "".join(
        f'<tr><td class="crit">{html.escape(criterion)}</td>'
        + "".join(f"<td>{html.escape(o['values'][i])}</td>" for o in data["options"])
        + "</tr>"
        for i, criterion in enumerate(data["criteria"])
    )
    return f"<table><tr><th></th>{head}</tr>{rows}</table>"


def render(kind: str, question: str, data: dict, citations, language: str, mode: str) -> bytes:
    title = TITLES[kind].get(language, TITLES[kind]["es"])
    head = f"<h1>{html.escape(question)}</h1>"
    if kind == "comparison":
        table = _table_html(data)
        height = base._measure(table, WIDTH - 2 * base.MARGIN, base.CSS + TABLE_CSS) + 12

        def draw(page, area):
            page.insert_htmlbox(area, table, css=base.CSS + TABLE_CSS)

        return base.compose(WIDTH, head, citations, language, mode, title, height, draw)
    if kind == "concept_map":
        return base.compose(WIDTH, head, citations, language, mode, title, _concept_map_height(data), lambda p, a: _concept_map(p, a, data))
    return base.compose(WIDTH, head, citations, language, mode, title, 470, lambda p, a: _ishikawa(p, a, data))


def render_message(kind: str, message) -> bytes:
    data = extract(kind, message.content, message.language)
    return render(kind, base.question_for(message), data, list(message.citations.all()), message.language, message.mode)
