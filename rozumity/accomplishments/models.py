from django.db import models
from django.utils.translation import gettext_lazy as _


class University(models.Model):
    title = models.CharField(max_length=128)
    
    class Meta:
        verbose_name = _('University')
        verbose_name_plural = _('Universities')
    
    def __str__(self):
        return self.title


class Speciality(models.Model):
    title = models.CharField(max_length=128)
    code_ua = models.SmallIntegerField()
    
    class Meta:
        verbose_name = _('Speciality')
        verbose_name_plural = _('Specialities')
    
    def __str__(self):
        return self.title


# TODO: possibility to upload or share a diploma or a certificate
class Education(models.Model):
    DEGREE_CHOICES = (
        (0, _('courses')), (1, _('undergraduate')), (2, _('specialist')), (3, _('master')), 
        (4, _('postgraduate')), (5, _('doctor'))
    )
    
    university = models.ForeignKey(University, on_delete=models.PROTECT)
    university_degree = models.SmallIntegerField(
        choices=DEGREE_CHOICES, default=0
    )
    speciality = models.ForeignKey('Speciality', on_delete=models.PROTECT, null=True)
    date_start = models.DateField()
    date_end = models.DateField()
    
    @property
    def education_duration(self):
        delta = self.date_start - self.date_end
        return round(delta.days / 365)
