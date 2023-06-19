from django.urls import path, include
from rest_framework import routers

from . import views

router = routers.DefaultRouter()
router_test = routers.DefaultRouter()
router.register(r"", views.UniversityViewSet, basename="universities")
router_test.register(r"", views.TestViewSet, basename="test")


urlpatterns = [
    path("universities/", include((router.urls, 'universities')), name='universities'),
    path("test/", include((router_test.urls, 'test')), name='test')
]
