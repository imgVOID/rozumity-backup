# python manage.py test
# python ../manage.py test rozumity
from django.test import TestCase
# from django.contrib.auth import get_user_model
from accomplishments.serializers import TestSerializer
from accomplishments.models import Test
from cities_light.models import City, Country


class SerializerTests(TestCase):
    serializer = TestSerializer
    queryset = Test.objects.prefetch_related('country').select_related(
        'city', 'city__subregion', 'city__region', 'city__country'
    )
    data = [{"type": "test", "attributes": {"title": "test1"}, 
             "relationships": {"city": {"data": {"type": "city","id": 1334}},
                               "country": {"data": [{"type": "country","id": 2},{"type": "country", "id": 27}]}}},
            {"type": "test", "id": 2, "attributes": {"title": "test2"},
             "relationships": {"city": {"data": {"type": "city","id": 245}},
                               "country": {"data": [{"type": "country","id": 2},{"type": "country", "id": 7}]}}}]
    
    async def test_serialize_obj(self):
        object = await self.queryset.acreate(**self.data[0]['attributes'])
        assert await self.serializer(object).data == {'data': {'type': 'test', 'id': 1, 'attributes': {'title': 'test1'}, 
                                                               'relationships': {'city': {'data': []}, 'country': {'data': []}}}}
    
    async def test_serialize_obj_relationships(self):
        countries_ids = [obj['id'] for obj in self.data[0]['relationships']['country']['data']]
        for id in countries_ids:
            await Country.objects.aget_or_create(id=id, name='test_country_' + str(id))
        countries = [obj async for obj in Country.objects.filter(id__in=countries_ids).all()]
        relationships = {}
        relationships['city'], _ = await City.objects.aget_or_create(
            name='test_city', 
            id=self.data[0]['relationships']['city']['data']['id'], 
            country=countries[0]
        )
        object = await self.queryset.acreate(
            **self.data[0]['attributes'], **relationships
        )
        await object.country.aset(countries)
        assert await self.serializer(object).data == {'data': {'type': 'test', 'id': 2, 'attributes': {'title': 'test1'}, 
                         'relationships': {'city': {'data': {'type': 'city', 'id': 1334}}, 
                                           'country': {'data': [{'type': 'country', 'id': 2}, {'type': 'country', 'id': 27}]}}}}