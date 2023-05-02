from django.urls import path

from accounts.views import db_populate_universities

urlpatterns = [
    path('db/populate/universities', db_populate_universities)
]
