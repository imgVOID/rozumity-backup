from django.contrib import admin

from .models import University, Test


@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
    pass

@admin.register(Test)
class TestAdmin(admin.ModelAdmin):
    pass
