from django import forms
from django.contrib import admin, messages

from .ingest import index_document
from .models import Document, file_sha256


class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ("file", "title", "authors", "year", "publisher", "url", "doi")

    def clean_file(self):
        upload = self.cleaned_data["file"]
        if "file" in self.changed_data:
            if not upload.name.lower().endswith(".pdf"):
                raise forms.ValidationError("Solo se aceptan archivos PDF.")
            digest = file_sha256(upload)
            if Document.objects.filter(sha256=digest).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError("Este PDF ya está en el corpus.")
            self.instance.sha256 = digest
        return upload


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    form = DocumentForm
    list_display = ("title", "authors", "year", "pages", "chunk_total", "status", "indexed_at")
    list_filter = ("status",)
    search_fields = ("title", "authors")
    readonly_fields = ("status", "error", "pages", "sha256", "indexed_at")
    actions = ("reindex",)

    @admin.display(description="fragmentos")
    def chunk_total(self, obj):
        return obj.chunks.count()

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if "file" in form.changed_data:
            self._index(request, obj)

    def _index(self, request, document):
        try:
            total = index_document(document)
            self.message_user(request, f"«{document}»: {total} fragmentos indexados.", messages.SUCCESS)
        except Exception as exc:  # el error queda visible en el documento y en el aviso
            self.message_user(request, f"«{document}»: {exc}", messages.ERROR)

    @admin.action(description="Volver a indexar")
    def reindex(self, request, queryset):
        for document in queryset:
            self._index(request, document)
