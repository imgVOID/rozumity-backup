from django.urls import path, include
from rest_framework import routers

from . import views

router = routers.DefaultRouter()
router.register(r"", views.UniversityViewSet, basename="universities")

urlpatterns = [
    path("universities/<slug:alpha2>/", include((router.urls, 'universities')), name='universities')
]
