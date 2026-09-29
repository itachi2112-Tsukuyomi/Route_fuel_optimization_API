from django.contrib import admin
from django.urls import include, path
from django.views.generic import RedirectView

urlpatterns = [
    path("", RedirectView.as_view(url="/api/route/map/?start=Chicago,+IL&finish=Los+Angeles,+CA")),
    path("admin/", admin.site.urls),
    path("api/", include("fuel.urls")),
]
