import uuid

from django.conf import settings
from django.db import models


class Mode(models.TextChoices):
    CONCEPTS = "A", "Consultar conceptos"
    CASES = "B", "Explorar casos"
    DILEMMAS = "C", "Analizar dilemas éticos"


class Conversation(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="conversations")
    title = models.CharField(max_length=120)
    mode = models.CharField(max_length=1, choices=Mode.choices, default=Mode.CONCEPTS)
    language = models.CharField(max_length=2, default="es")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return self.title


class Message(models.Model):
    class Role(models.TextChoices):
        USER = "user"
        ASSISTANT = "assistant"

    class Status(models.TextChoices):
        ANSWER = "answer"
        GREETING = "greeting"
        FAREWELL = "farewell"
        ABOUT = "about"
        AMBIGUOUS = "ambiguous"
        OUT_OF_DOMAIN = "out_of_domain"
        NO_EVIDENCE = "no_evidence"
        STOPPED = "stopped"

    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField()
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.ANSWER)
    mode = models.CharField(max_length=1, choices=Mode.choices, default=Mode.CONCEPTS)
    language = models.CharField(max_length=2, default="es")
    model_name = models.CharField(max_length=80, blank=True)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    feedback = models.SmallIntegerField(default=0)  # -1, 0 o 1
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class Citation(models.Model):
    """Copia lo esencial de la fuente para que el historial sobreviva si cambia el corpus."""

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="citations")
    chunk = models.ForeignKey("corpus.Chunk", null=True, on_delete=models.SET_NULL)
    number = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=400)
    page = models.PositiveIntegerField()
    similarity = models.FloatField()
    quote = models.TextField()

    class Meta:
        ordering = ["number"]


class GeneratedAsset(models.Model):
    class Kind(models.TextChoices):
        IMAGE = "image"
        AUDIO = "audio"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="assets")
    kind = models.CharField(max_length=5, choices=Kind.choices)
    file = models.FileField(upload_to="generated/")
    model_name = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["message", "kind"], name="one_asset_per_kind")]
