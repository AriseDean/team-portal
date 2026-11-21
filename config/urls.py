from django.conf import settings
from django.contrib import admin
from django.urls import include, path

admin.site.site_header = "Team Portal admin"
admin.site.site_title = "Team Portal"
admin.site.index_title = "Dashboard"

urlpatterns = [
    path(settings.ADMIN_URL, admin.site.urls),
    path("", include("portal.urls")),
]

handler404 = "portal.views.not_found"
handler500 = "portal.views.server_error"
