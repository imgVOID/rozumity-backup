from django.contrib import admin

from accounts.models import User, ClientProfile, ExpertProfile, University


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    pass


@admin.register(ClientProfile)
class ClientProfileAdmin(admin.ModelAdmin):
    pass


@admin.register(ExpertProfile)
class ExpertProfileAdmin(admin.ModelAdmin):
    pass


@admin.register(University)
class UniversityAdmin(admin.ModelAdmin):
    pass
