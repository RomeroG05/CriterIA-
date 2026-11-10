import csv
import unicodedata
from pathlib import Path

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand

from corpus.ingest import index_document
from corpus.models import Document, file_sha256

FIELDS = ("title", "authors", "year", "publisher", "url", "doi")


class Command(BaseCommand):
    help = "Indexa los PDF de una carpeta (por defecto corpus_pdfs/). Lee metadata.csv si existe."

    def add_arguments(self, parser):
        parser.add_argument("folder", nargs="?", default=str(settings.CORPUS_DIR))
        parser.add_argument("--reindex", action="store_true", help="Reprocesa también los PDF ya indexados.")

    def _metadata(self, folder: Path) -> dict[str, dict]:
        path = folder / "metadata.csv"
        if not path.exists():
            return {}
        with path.open(encoding="utf-8-sig", newline="") as handle:
            return {unicodedata.normalize("NFC", row["filename"].strip()): row for row in csv.DictReader(handle)}

    def handle(self, *args, folder, reindex, **options):
        folder = Path(folder)
        metadata = self._metadata(folder)
        pdfs = sorted(p for p in folder.iterdir() if p.suffix.lower() == ".pdf")
        if not pdfs:
            self.stdout.write(self.style.WARNING(f"No hay PDF en {folder}"))
            return
        failed = 0
        for path in pdfs:
            with path.open("rb") as handle:
                digest = file_sha256(handle)
                document = Document.objects.filter(sha256=digest).first()
                if document is None:
                    document = Document(sha256=digest)
                    document.file.save(path.name, File(handle), save=False)
                    new = True
                else:
                    new = False
            for field in FIELDS:
                value = (metadata.get(unicodedata.normalize("NFC", path.name), {}).get(field) or "").strip()
                if value:
                    setattr(document, field, int(value) if field == "year" else value)
            document.save()
            if not new and not reindex and document.status == Document.Status.READY:
                self.stdout.write(f"= {path.name}: ya indexado (metadatos actualizados)")
                continue
            try:
                total = index_document(document)
                self.stdout.write(self.style.SUCCESS(f"+ {path.name}: {document.pages} páginas, {total} fragmentos"))
            except Exception as exc:
                failed += 1
                self.stdout.write(self.style.ERROR(f"x {path.name}: {exc}"))
        if failed:
            self.stdout.write(self.style.ERROR(f"{failed} documento(s) con error."))
