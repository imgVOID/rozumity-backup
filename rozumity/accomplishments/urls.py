from django.urls import path, include
from rest_framework import routers

from . import views

router = routers.DefaultRouter()
router.register(r"ukraine", views.DBUniversity, basename="universities_ukraine")

urlpatterns = [
    path("universities/", include(router.urls))
]
