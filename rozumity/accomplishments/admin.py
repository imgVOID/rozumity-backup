from django.contrib import admin

from .models import University


@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
    pass
