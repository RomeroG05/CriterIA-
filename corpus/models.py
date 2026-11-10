import hashlib

from django.db import models
from django.db.models.signals import post_delete
from django.dispatch import receiver


def file_sha256(fileobj) -> str:
    digest = hashlib.sha256()
    fileobj.seek(0)
    for block in iter(lambda: fileobj.read(1 << 20), b""):
        digest.update(block)
    fileobj.seek(0)
    return digest.hexdigest()


class Document(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pendiente"
        PROCESSING = "processing", "Procesando"
        READY = "ready", "Listo"
        ERROR = "error", "Error"

    title = models.CharField("título", max_length=400, blank=True, help_text="Vacío: se toma del PDF.")
    authors = models.CharField("autores", max_length=600, blank=True, help_text="Apellido, N.; Apellido, M.")
    year = models.PositiveSmallIntegerField("año", null=True, blank=True)
    publisher = models.CharField("editorial o institución", max_length=300, blank=True)
    url = models.URLField("URL", max_length=500, blank=True)
    doi = models.CharField("DOI", max_length=200, blank=True)
    file = models.FileField("PDF", upload_to="corpus/")
    sha256 = models.CharField(max_length=64, unique=True, editable=False)
    pages = models.PositiveIntegerField("páginas", default=0, editable=False)
    status = models.CharField("estado", max_length=12, choices=Status.choices, default=Status.PENDING, editable=False)
    error = models.TextField(blank=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    indexed_at = models.DateTimeField("indexado", null=True, blank=True, editable=False)

    class Meta:
        ordering = ["title"]
        verbose_name = "documento"

    def __str__(self):
        return self.title or self.file.name


class Chunk(models.Model):
    document = models.ForeignKey(Document, on_delete=models.CASCADE, related_name="chunks")
    page = models.PositiveIntegerField()  # numeración desde 1
    order = models.PositiveIntegerField()
    text = models.TextField()
    bboxes = models.JSONField(default=list)  # rectángulos normalizados (0-1) para resaltar el pasaje
    embedding = models.BinaryField()

    class Meta:
        ordering = ["document_id", "order"]

    def __str__(self):
        return f"{self.document_id}:p{self.page}#{self.order}"


@receiver(post_delete, sender=Document)
def _remove_pdf(sender, instance, **kwargs):
    if instance.file:
        instance.file.delete(save=False)
