"""Referencias bibliográficas generadas solo con los metadatos reales del documento."""
import html
import re

NO_DATE = {"es": "s. f.", "en": "n.d.", "pt": "s.d."}
RETRIEVED = {"es": "Recuperado de", "en": "Retrieved from", "pt": "Recuperado de"}


def _authors(document) -> list[str]:
    return [a.strip() for a in document.authors.split(";") if a.strip()]


def _link(document) -> str:
    return f"https://doi.org/{document.doi}" if document.doi else document.url


def _key(document) -> str:
    first = (_authors(document) or [document.title or "doc"])[0].split(",")[0]
    return re.sub(r"\W+", "", first.lower())[:20] + str(document.year or "sf")


def _apa(document, lang) -> str:
    names = _authors(document)
    who = html.escape(", ".join(names[:-1]) + ", &amp; " + names[-1] if len(names) > 1 else (names[0] if names else ""))
    who = who.replace("&amp;amp;", "&amp;")
    year = document.year or NO_DATE[lang]
    title = f"<i>{html.escape(document.title)}</i>."
    head = f"{who} ({year}). {title}" if who else f"{title} ({year})."
    tail = " ".join(x for x in (html.escape(document.publisher) + "." if document.publisher else "", _link(document)) if x)
    return f"{head} {tail}".strip()


def _chicago(document, lang) -> str:
    names = _authors(document)
    who = html.escape(" and ".join(names))
    year = document.year or NO_DATE[lang]
    parts = [f"{who}." if who else "", f"<i>{html.escape(document.title)}</i>.", html.escape(document.publisher), f"{year}."]
    return " ".join(p for p in parts if p) + (f" {_link(document)}." if _link(document) else "")


def _mla(document, lang) -> str:
    names = _authors(document)
    who = html.escape(names[0] + (", et al." if len(names) > 2 else (f", and {names[1]}." if len(names) == 2 else ".")) if names else "")
    parts = [who, f"<i>{html.escape(document.title)}</i>.", html.escape(document.publisher) + "," if document.publisher else "", f"{document.year or NO_DATE[lang]}."]
    return " ".join(p for p in parts if p) + (f" {_link(document)}." if _link(document) else "")


def _bibtex(document) -> str:
    fields = [("title", document.title), ("author", " and ".join(_authors(document))), ("year", document.year),
              ("publisher", document.publisher), ("doi", document.doi), ("url", document.url)]
    body = ",\n".join(f"  {name} = {{{value}}}" for name, value in fields if value)
    return html.escape(f"@misc{{{_key(document)},\n{body}\n}}")


def _ris(document) -> str:
    lines = ["TY  - GEN", f"TI  - {document.title}"]
    lines += [f"AU  - {name}" for name in _authors(document)]
    lines += [f"{tag}  - {value}" for tag, value in (("PY", document.year), ("PB", document.publisher), ("DO", document.doi), ("UR", document.url)) if value]
    return html.escape("\n".join([*lines, "ER  - "]))


def in_text(document, page: int, lang: str) -> str:
    names = _authors(document)
    if not names:
        who = document.title[:40]
    else:
        surnames = [n.split(",")[0] for n in names]
        who = surnames[0] if len(surnames) == 1 else (f"{surnames[0]} & {surnames[1]}" if len(surnames) == 2 else f"{surnames[0]} et al.")
    return f"({who}, {document.year or NO_DATE[lang]}, p. {page})"


def build(document, page: int, lang: str) -> dict:
    lang = lang if lang in NO_DATE else "es"
    return {
        "in_text": in_text(document, page, lang),
        "styles": [
            {"id": "apa", "label": "APA 7", "html": _apa(document, lang)},
            {"id": "chicago", "label": "Chicago", "html": _chicago(document, lang)},
            {"id": "mla", "label": "MLA 9", "html": _mla(document, lang)},
            {"id": "bibtex", "label": "BibTeX", "html": _bibtex(document), "code": True},
            {"id": "ris", "label": "RIS", "html": _ris(document), "code": True},
        ],
        "incomplete": not (document.authors and document.year),
    }
