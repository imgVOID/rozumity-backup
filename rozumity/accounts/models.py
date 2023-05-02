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

    class Meta:
        verbose_name = _('User')
        verbose_name_plural = _('Users')

# TODO: subscription plans
class AbstractProfile(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    first_name = models.CharField(max_length=32)
    last_name = models.CharField(max_length=32)
    country = models.ForeignKey('cities_light.Country', on_delete=models.SET_NULL, null=True, blank=True)
    region = models.ForeignKey('cities_light.Region', on_delete=models.SET_NULL, null=True, blank=True)
    city = models.ForeignKey('cities_light.City', on_delete=models.SET_NULL, null=True, blank=True)
    
    @property
    def name(self):
        return f'{self.first_name} {self.last_name}'
    
    @property
    def name_reversed(self):
        return f'{self.last_name} {self.first_name}'
    
    @property
    def address(self):
        return f'{str(self.city)}, {str(self.region)}, {str(self.country)}'


class ClientProfile(AbstractProfile):
    profile = models.ForeignKey(AbstractProfile, on_delete=models.CASCADE, related_name='client_profile_set')
    
    class Meta:
        verbose_name = _("Client's Profile")
        verbose_name_plural = _("Clients' Profiles")
    
    def __str__(self):
        return self.user.email


class University(models.Model):
    title = models.CharField(max_length=128)
    
    class Meta:
        verbose_name = _('University')
        verbose_name_plural = _('Universities')
    
    def __str__(self):
        return self.title


class Education(models.Model):
    university = models.ForeignKey(University, on_delete=models.PROTECT)
    university_degree = models.SmallIntegerField(
        choices=((0, _('bachelor')), (1, _('master')), (2, _('doctor'))), default=0
    )
    date_start = models.DateField()
    date_end = models.DateField()
    
    @property
    def education_duration(self):
        delta = self.date_start - self.date_end
        return delta.days


class ExpertProfile(AbstractProfile):
    profile = models.ForeignKey(AbstractProfile, on_delete=models.CASCADE, related_name='expert_profile_set')
    education = models.ManyToManyField(Education, blank=True)
    education_extra = models.TextField(max_length=500, null=True, blank=True)
    
    class Meta:
        verbose_name = _("Expert's Profile")
        verbose_name_plural = _("Experts' Profiles")
    
    def __str__(self):
        return self.user.email
