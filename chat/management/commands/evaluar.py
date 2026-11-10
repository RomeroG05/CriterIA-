"""Evalúa al asistente con preguntas de control y el modelo real.

«dentro»: la respuesta está en el corpus; se espera respuesta fundamentada en el idioma de la pregunta.
«fuera»: el corpus no la responde; se espera cualquier limitación (sin evidencia, fuera de dominio...).
"""
import csv
import re
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from chat import language, pipeline

DEFAULT = Path(settings.BASE_DIR) / "evaluacion" / "preguntas.csv"


class Command(BaseCommand):
    help = "Ejecuta las preguntas de control de evaluacion/preguntas.csv y resume los aciertos."

    def add_arguments(self, parser):
        parser.add_argument("archivo", nargs="?", default=str(DEFAULT))
        parser.add_argument("--repeticiones", type=int, default=1, help="Veces que se repite cada pregunta.")
        parser.add_argument("--detalle", action="store_true", help="Muestra el inicio de cada respuesta.")

    def handle(self, *args, archivo, repeticiones, detalle, **options):
        with open(archivo, encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        totals = {"dentro": [0, 0], "fuera": [0, 0]}
        for row in rows:
            group, mode, expected, question = row["grupo"], row["modo"], row["idioma"], row["pregunta"]
            for _ in range(repeticiones):
                started = time.monotonic()
                final = list(pipeline.run(question, mode, [], expected))[-1]
                status = final.get("status", "error")
                content = final.get("content", "")
                got = language.dominant(re.sub(r"\[\d+\]|[#*_]", " ", content)) if status == "answer" else "-"
                ok = (status == "answer" and got == expected) if group == "dentro" else status != "answer"
                totals[group][0] += ok
                totals[group][1] += 1
                mark = self.style.SUCCESS("OK  ") if ok else self.style.ERROR("FALLA")
                self.stdout.write(f"{mark} {group:6} [{expected}→{got}] {status:13} {time.monotonic() - started:5.1f}s  {question[:70]}")
                if detalle or not ok:
                    self.stdout.write(f"        {content[:200]!r}")
        inside, outside = totals["dentro"], totals["fuera"]
        self.stdout.write("")
        self.stdout.write(f"Dentro del corpus: {inside[0]}/{inside[1]} respondidas en el idioma de la pregunta")
        self.stdout.write(f"Fuera del corpus:  {outside[0]}/{outside[1]} rechazadas sin inventar")
