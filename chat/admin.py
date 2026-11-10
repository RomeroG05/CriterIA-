from django.contrib import admin

from .models import Citation, Conversation, GeneratedAsset, Message


class MessageInline(admin.TabularInline):
    model = Message
    extra = 0
    fields = ("role", "status", "language", "content", "feedback", "latency_ms")
    readonly_fields = fields
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("title", "user", "mode", "language", "updated_at")
    list_filter = ("mode", "language")
    search_fields = ("title", "user__email")
    inlines = [MessageInline]


admin.site.register(Citation)
admin.site.register(GeneratedAsset)
admin.site.site_header = "CriterIA · Administración"
admin.site.site_title = "CriterIA"
