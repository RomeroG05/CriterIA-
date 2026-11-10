from django.urls import path

from . import views

app_name = "corpus"
urlpatterns = [
    path("", views.library, name="library"),
    path("<int:pk>/archivo/", views.document_file, name="file"),
    path("<int:pk>/pagina/<int:page>.jpg", views.page_image, name="page"),
    path("fuente/<int:chunk_id>/", views.source_detail, name="source"),
]
