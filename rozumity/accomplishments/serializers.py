
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
    def __init__(self, objects, related=None, request=None):
        super().__init__(
            objects, UniversityRelJSONAPISerializer,
            related, CountryJSONAPISerializer, request
        )
        self._related_url = 'locations/countries/'
    
    async def get_link_related(self):
        url = await self.get_link()
        return url.split('api/')[0] + 'api/' + self._related_url


# TODO: to make a manager for the patch (bulk deletion), only ID and type
class JSONAPIUniversityLimitedManager(JSONAPIManager):
    def __init__(self, objects):
        super().__init__(
            objects, UniversityJSONAPISerializer
        )
