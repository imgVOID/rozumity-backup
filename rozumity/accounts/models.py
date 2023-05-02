from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _



from .managers import CustomUserManager


class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(_("email address"), unique=True, max_length=64)
    is_staff = models.BooleanField(default=False)
    is_client = models.BooleanField(default=False)
    is_expert = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    date_joined = models.DateTimeField(default=timezone.now)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = CustomUserManager()

    def __str__(self):
        return self.email

# TODO: subscription plans
class AbstractProfile(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    first_name = models.CharField(max_length=32)
    last_name = models.CharField(max_length=32)
#    country = models.ForeignKey('cities_light.Country', on_delete=models.SET_NULL, null=True, blank=True) 
#    city = models.ForeignKey('cities_light.City', on_delete=models.SET_NULL, null=True, blank=True)
    
    @property
    def name(self):
        return f'{self.first_name} {self.last_name}'
    
    @property
    def name_reversed(self):
        return f'{self.last_name} {self.first_name}'
        


class ClientProfile(AbstractProfile):
    profile = models.ForeignKey(AbstractProfile, on_delete=models.CASCADE, related_name='client_profile_set')


class ExpertProfile(AbstractProfile):
    profile = models.ForeignKey(AbstractProfile, on_delete=models.CASCADE, related_name='expert_profile_set')

