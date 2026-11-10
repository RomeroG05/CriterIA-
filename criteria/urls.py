from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("i18n/", include("django.conf.urls.i18n")),
    path("cuenta/", include("accounts.urls")),
    path("corpus/", include("corpus.urls")),
    path("", include("chat.urls")),
]
