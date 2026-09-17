from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from core.error_handlers import preview_error


handler400 = "core.error_handlers.error_400"
handler403 = "core.error_handlers.error_403"
handler404 = "core.error_handlers.error_404"
handler500 = "core.error_handlers.error_500"


urlpatterns = [
    path(
        "admin/",
        admin.site.urls,
    ),
    path(
        "",
        include(
            "pedidos.urls"
        ),
    ),
]


if settings.DEBUG:
    urlpatterns += [
        path(
            "__dev__/error/<int:status_code>/",
            preview_error,
            name="dev_error_preview",
        ),
    ]

    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )
