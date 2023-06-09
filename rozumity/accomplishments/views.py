import time
from django.core.exceptions import ObjectDoesNotExist
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from adrf.viewsets import ViewSet
from aiofiles import open

from cities_light.models import Country
from rozumity.paginations import LimitOffsetAsyncPagination, get_current_site

from .models import University, Test
from .permissions import UniversityPermission
from .serializers import UniversitySerializer, TestSerializer


class TestViewSet(ViewSet):
    permission_classes=[UniversityPermission]
    authentication_classes = [SessionAuthentication]
    pagination_class = LimitOffsetAsyncPagination
    queryset = Test.objects.prefetch_related('country').select_related(
        'city__subregion', 'city__region', 'city__country'
    )
    
    async def list(self, request):
        objects = await self.pagination_class.paginate_queryset(
            self.queryset.order_by('id'), request=request
        )
        objects = [university async for university in objects]
        objects_length = len(objects)
        if objects_length:
            data = await TestSerializer(
                objects, many=True, context={'request': request}, 
                max_length=objects_length, min_length=objects_length
            ).data
            response = await self.pagination_class.get_paginated_response(data)
            # TODO: write unit tests
            serializer_field = await TestSerializer(objects, many=True)['attributes']
            serializer_obj_representation = TestSerializer(objects, many=True).__repr__()
            async for test in TestSerializer(objects, many=True):
                assert type(test) == list and len(test) > 1
            assert type(serializer_field) == list and len(serializer_field) > 1
            assert type(serializer_obj_representation) == str and len(serializer_obj_representation) > 10
            # print(serializer_obj_representation)
        else:
            response = Response(status=404)
        return response
    
    async def create(self, request):
        startT = time.time()
        serializer_full = TestSerializer(
            data=request.data, many=False, context={'request': request}
        )
        if await serializer_full.is_valid():
            response_data = await serializer_full.data
            status = 200
        else:
            response_data = await serializer_full.errors
            status = 403
        print(f'function time: {time.time() - startT}ms')
        return Response(data=response_data, status=status)


# TODO: retrieve
# TODO: serialize when there are many foreign key fields
class UniversityViewSet(ViewSet):
    permission_classes=[UniversityPermission]
    authentication_classes = [SessionAuthentication]
    pagination_class = LimitOffsetAsyncPagination
    queryset = University.objects.select_related('country')
    
    async def list(self, request, alpha2):
        if len(alpha2) != 2:
            return Response(status=404, data={"errors": [{
                "status": 400, "title": "Bad request",
                "detail": f'Please enter a valid alpha-2 country code.'
            }]})
        objects = self.queryset.filter(country__code2=alpha2.upper()).order_by('id')
        objects = await self.pagination_class.paginate_queryset(
            objects, request=request
        )
        objects = [university async for university in objects]
        objects_length = len(objects)
        if objects_length:
            startT = time.time()
            data = await UniversitySerializer(
                objects, many=True, context={'request': request},
                max_length=objects_length, min_length=objects_length
            ).data
            print(f'function time: {time.time() - startT}ms')
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
