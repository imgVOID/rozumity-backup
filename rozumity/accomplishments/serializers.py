
from rozumity.serializers import JSONAPI
from cities_light.models import Country
from .models import University


class UniversityJSONAPI(JSONAPI):
    class Meta:
        model = University


class CountryJSONAPI(JSONAPI):
    class Meta:
        model = Country
