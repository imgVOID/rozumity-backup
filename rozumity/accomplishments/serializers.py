
from rozumity.serializers import JSONAPISerializer, JSONAPIRelSerializer, JSONAPIManager
from cities_light.models import Country
from .models import University


class UniversityJSONAPISerializer(JSONAPISerializer):
    class Meta:
        model = University


class UniversityRelJSONAPISerializer(JSONAPIRelSerializer):
    class Meta:
        model = University


class CountryJSONAPISerializer(JSONAPISerializer):
    class Meta:
        model = Country


class JSONAPIUniversityManager(JSONAPIManager):
    def __init__(self, objects, related):
        super().__init__(
            objects, UniversityRelJSONAPISerializer,
            related, CountryJSONAPISerializer
        )


# TODO: to make a manager for the patch (bulk deletion), only ID and type
class JSONAPIUniversityLimitedManager(JSONAPIManager):
    def __init__(self, objects):
        super().__init__(
            objects, UniversityJSONAPISerializer
        )
