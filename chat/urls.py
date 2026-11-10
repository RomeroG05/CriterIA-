from django.urls import path

from . import views

app_name = "chat"
urlpatterns = [
    path("", views.home, name="home"),
    path("guia/", views.guide, name="guide"),
    path("c/<uuid:pk>/", views.conversation_detail, name="conversation"),
    path("c/<uuid:pk>/renombrar/", views.conversation_rename, name="rename"),
    path("c/<uuid:pk>/eliminar/", views.conversation_delete, name="delete"),
    path("c/<uuid:pk>/exportar.pdf", views.conversation_export, name="export"),
    path("recurso/<uuid:pk>/", views.asset_file, name="asset"),
    path("api/estado/", views.api_status, name="status"),
    path("api/chat/", views.api_chat, name="api_chat"),
    path("api/voz/", views.api_transcribe, name="transcribe"),
    path("api/mensajes/<int:pk>/valorar/", views.api_feedback, name="feedback"),
    path("api/mensajes/<int:pk>/generar/<str:kind>/", views.api_generate, name="generate"),
]
