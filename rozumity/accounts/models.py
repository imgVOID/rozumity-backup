from datetime import date, timedelta

from django.db import models
from django.utils import timezone
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.contrib.postgres.fields import ArrayField
from django.utils.translation import gettext_lazy as _

from .managers import CustomUserManager
from accomplishments.models import Education


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
    GENDER_CHOICES = (
        (0, _('male')), (1, _('female')), (2, _('non-binary')), (3, _('transgender')), 
        (4, _('intersex')), (5, _('prefer not to say'))
    )
    def get_default_gender():
        return (5,)
    
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    first_name = models.CharField(max_length=32)
    last_name = models.CharField(max_length=32)
    gender = ArrayField(models.SmallIntegerField(choices=GENDER_CHOICES, default=5), 
                        default=get_default_gender, max_length=2, size=2)
    country = models.ForeignKey('cities_light.Country', on_delete=models.SET_NULL, null=True, blank=True)
    region = models.ForeignKey('cities_light.Region', on_delete=models.SET_NULL, null=True, blank=True)
    city = models.ForeignKey('cities_light.City', on_delete=models.SET_NULL, null=True, blank=True)
    date_birth = models.DateField(default=date.today()-timedelta(days=18*365))
    
    @property
    def name(self):
        return f'{self.first_name} {self.last_name}'

    @property
    def name_reversed(self):
        return f'{self.last_name} {self.first_name}'

    @property
    def address(self):
        return f'{str(self.city)}, {str(self.region)}, {str(self.country)}'

    @property
    def age(self):
        return (date.today() - self.birth_date).days / 365

    @property
    def is_adult(self):
        return True if self.age > 18 else False
    
    @property
    def gender_verbose(self):
        genders = dict(self.GENDER_CHOICES)
        return ', '.join([genders[gender] for gender in self.gender])


class ClientProfile(AbstractProfile):
    class Meta:
        verbose_name = _("Client's Profile")
        verbose_name_plural = _("Clients' Profiles")
    
    def __str__(self):
        return self.user.email


class ExpertProfile(AbstractProfile):
    education = models.ManyToManyField(Education, blank=True)
    education_extra = models.TextField(max_length=500, null=True, blank=True)
    
    class Meta:
        verbose_name = _("Expert's Profile")
        verbose_name_plural = _("Experts' Profiles")
    
    def __str__(self):
        return self.user.email
