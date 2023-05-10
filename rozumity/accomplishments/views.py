from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Prefetch
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from adrf.viewsets import ViewSet
from aiofiles import open

from cities_light.models import Country
from rozumity.paginations import LimitOffsetAsyncPagination
from rozumity.serializers import JSONAPISerializer

from .models import University, Test
from .permissions import UniversityPermission


class TestViewSet(ViewSet):
    permission_classes=[UniversityPermission]
    authentication_classes = [SessionAuthentication]
    pagination_class = LimitOffsetAsyncPagination
    
    async def list(self, request):
        objects = []
        async for university in Test.objects.prefetch_related(
            Prefetch('country', to_attr='country_set')
        ).select_related('city__subregion', 'city__region', 'city__country'):
            objects.append(university)
        if objects:
            data = JSONAPISerializer(
                    await self.pagination_class.paginate_queryset(
                        queryset=objects, request=request
                    ), many=True
                ).data
            response = await self.pagination_class.get_paginated_response(data)
        else:
            response = Response(status=404)
        return response

# TODO: retrieve
# TODO: serialize when there are many foreign key fields
class UniversityViewSet(ViewSet):
    permission_classes=[UniversityPermission]
    authentication_classes = [SessionAuthentication]
    pagination_class = LimitOffsetAsyncPagination
    
    async def list(self, request, alpha2):
        if len(alpha2) != 2:
            return Response(status=404, data={"errors": [{
                "status": 400, "title": "Bad request",
                "detail": f'Please enter a valid alpha-2 country code.'
            }]})
        else:
            objects = []
        async for university in University.objects.prefetch_related('country').filter(country__code2=alpha2.upper()):
            objects.append(university)
        if objects:
            data = JSONAPISerializer(
                    await self.pagination_class.paginate_queryset(
                        queryset=objects, request=request
                    ), many=True
                ).data
            response = await self.pagination_class.get_paginated_response(data)
        else:
            response = Response(status=404, data={"errors": [{
                "status": 404, "title": "Not Found",
                "detail": 'There are no universities for Sorry, '
                f'but the country with alpha2 code {alpha2.upper()} is not supported.'
            }]})
        return response
    
    async def create(self, request, alpha2):
        alpha2 = alpha2.upper()
        if len(alpha2) != 2:
            return Response(status=404, data={"errors": [{
                "status": 400, "title": "Bad request",
                "detail": f'Please enter a valid alpha-2 country code.'
            }]})
        elif alpha2 not in ['UA']:
            return Response(status=404, data={"errors": [{
                "status": 404, "title": "Not Found",
                "detail": f'Sorry, but the country is not supported.'
            }]})
        else:
            country = await Country.objects.aget(code2=alpha2.upper())
            objects = []
        async with open(
            f'accomplishments/fixtures/universities_{alpha2.lower()}.txt', 
            mode="r", encoding="utf-8"
        ) as data:
            async for line in data:
                line = line.strip()
                title = line.split(';')[0]
                obj, created = await University.objects.aget_or_create(title=title, country=country)
                if created:
                    objects.append(obj)
        if objects:
            data = await JSONAPIUniversityManager(
                objects, related=country
            ).data
            response = Response(status=201, data=data)
        else:
            response = Response(status=409, data={"errors": [{
                "status": 409, "title": "Conflict", 
                "detail": f'The database already contains the provided objects.'
            }]})
        return response
    
    async def put(self, request, alpha2):
        if len(alpha2) != 2:
            return Response(status=404, data={"errors": [{
                "status": 400, "title": "Bad request",
                "detail": f'Please enter a valid alpha-2 country code.'
            }]})
        elif alpha2.upper() not in ['UA']:
            return Response(status=404, data={"errors": [{
                "status": 404, "title": "Not Found",
                "detail": f'Sorry, but the country is not supported.'
            }]})
        else:
            objects = []
        async with open(
            f'accomplishments/fixtures/universities_{alpha2.lower()}.txt', 
            mode="r", encoding="utf-8"
        ) as data:
            async for line in data:
                line = line.strip()
                title = line.split(';')[0]
                try:
                    obj = await University.objects.aget(title=title)
                except ObjectDoesNotExist:
                    pass
                else:
                    university = UniversityJSONAPISerializer(obj).data
                    del university['attributes']
                    await obj.adelete()
                    objects.append(university)
        if objects:
            response = Response(status=204, data={'data': objects})
        else:
            response = Response(status=404, data={
                "success":"false", "errors": [{
                    "status": 404, "title": "Not Found", 
                    "detail": f'There are no objects with the specified titles.'
                }]
            })
        return response

#TODO: API for specialities
