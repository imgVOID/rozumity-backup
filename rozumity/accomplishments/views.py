import time
from django.core.exceptions import ObjectDoesNotExist
from django.http.response import HttpResponseRedirect
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.reverse import reverse
from adrf.viewsets import ViewSet
from asgiref.sync import sync_to_async
from aiofiles import open

from cities_light.models import Country
from rozumity.paginations import LimitOffsetAsyncPagination

from .models import University, Test
from .permissions import UniversityPermission
from .serializers import UniversitySerializer, TestSerializer

reverse = sync_to_async(reverse)


class TestViewSet(ViewSet):
    permission_classes=[UniversityPermission]
    authentication_classes = [SessionAuthentication]
    pagination_class = LimitOffsetAsyncPagination
    queryset = Test.objects.prefetch_related('country').select_related(
        'city', 'city__subregion', 'city__region', 'city__country'
    )
    
    async def retrieve(self, request, pk):
        try:
            object = await self.queryset.aget(id=pk)
        except ObjectDoesNotExist:
            response = Response({'data': None}, status=404)
        else:
            response = Response(await TestSerializer(
                object, context={'request': request}
            ).data, status=200)
        return response
    
    async def list(self, request):
        filter_params = {}
        for key, val in request.query_params.items():
            if not key.startswith('filter['):
                continue
            key = key.split('[')[-1].replace(']', '')
            if '__' in key:
                split_key = key.split('__')
                key, lookup = split_key[0], '__' + split_key[1]
            else:
                lookup = '__in'
            try:
                is_relation = bool(getattr(
                    self.queryset.model, key, None
                ).field.remote_field)
            except AttributeError:
                continue
            key = key + '__id' + lookup if is_relation else key + lookup
            if ',' not in val and lookup != '__in' and val.isnumeric():
                val = int(val)
            elif lookup == '__range':
                split = val.split(',')
                val = [split[0], split[1]]
            else:
                val = val.split(',')
            filter_params.update({key: val})
        objects = await self.pagination_class.paginate_queryset(
            self.queryset.filter(**filter_params).order_by('id'), request=request
        )
        data = await TestSerializer(
            objects, many=True, context={'request': request}
        ).data
        # TODO: write unit tests
        serializer_field = await TestSerializer(objects, many=True)['attributes']
        serializer_obj_representation = TestSerializer(objects, many=True).__repr__()
        async for test in TestSerializer(objects, many=True):
            assert type(test) == list
        assert type(serializer_field) == list
        assert type(serializer_obj_representation) == str and len(serializer_obj_representation) > 2
        # print(serializer_obj_representation)
        if data['data']:
            response = await self.pagination_class.get_paginated_response(data)
        else:
            response = Response({'data': []})
        return response
    
    async def create(self, request):
        startT = time.time()
        data = request.data
        is_many = True if 'data' in data.keys() and type(data['data']) == list else False
        serializer_full = TestSerializer(
            data=data, many=is_many, context={'request': request}
        )
        if await serializer_full.is_valid():
            response_data = await serializer_full.validated_data
            status = 200
        else:
            response_data = await serializer_full.errors
            status = 403
        print(f'function time: {time.time() - startT}ms')
        return Response(data=response_data, status=status)
    
    @action(methods=["get"], detail=False, url_path=r'(?P<pk>\d+)/(?P<field_name>\w+)', url_name="related")
    async def related(self, request, *args, **kwargs):
        object = await self.queryset.aget(id=kwargs['pk'])
        try:
            field_name = kwargs['field_name']
            field = getattr(object, field_name)
        except AttributeError:
            return Response({'data': None}, status=404)
        serializer_field = TestSerializer.Relationships._declared_fields[kwargs['field_name']]
        if hasattr(field, 'all'):
            link = '{}?filter[id]={}'.format(
                await reverse(
                    getattr(serializer_field.child, 'view_name').replace('detail', 'list'),
                    request=request
                ),
                ",".join([
                    str(obj.id) async for obj in 
                    await sync_to_async(getattr(object, field_name).all)()
                ])
            )
        elif field:
            link = await reverse(
                getattr(serializer_field, 'view_name'), 
                args=[getattr(object, field_name).id], 
                request=request
            )
        return HttpResponseRedirect(link)
    
    @action(methods=["get"], detail=False, url_path=r'(?P<pk>\d+)/relationships/(?P<field_name>\w+)', url_name="self")
    async def self(self, request, *args, **kwargs):
        object = await self.queryset.aget(id=kwargs['pk'])
        try:
            field_name = kwargs['field_name']
            field = getattr(object, field_name)
        except AttributeError:
            return Response({'data': None}, status=404)
        serializer_field = TestSerializer.Relationships._declared_fields[field_name]
        if hasattr(serializer_field, 'child'):
            view_name = getattr(serializer_field.child, 'view_name')
        else:
            view_name = getattr(serializer_field, 'view_name')
        if hasattr(field, 'all'):
            data = []
            async for obj in await sync_to_async(field.all)():
                obj_data = await TestSerializer.ObjectId(obj).data
                obj_data.update({'links': {'self': await reverse(
                    serializer_field.child.view_name, args=[obj.id], request=request
                )}})
                data.append(obj_data)
            return Response(data={'data': data})
        else:
            data = await TestSerializer.ObjectId(field).data
            data['links'] = {}
            data['links']['self'] = await reverse(view_name, args=[field.id], request=request)
            return Response(data={'data': data})


class UniversityViewSet(ViewSet):
    permission_classes=[UniversityPermission]
    authentication_classes = [SessionAuthentication]
    pagination_class = LimitOffsetAsyncPagination
    queryset = University.objects.select_related('country')
    
    async def retrieve(self, request, pk):
        try:
            objects = await self.queryset.aget(id=pk)
        except ObjectDoesNotExist:
            response = Response(data={'data': None}, status=404)
        else:
            response = Response(await UniversitySerializer(
                objects, context={'request': request}
            ).data, status=200)
        return response
    
    async def list(self, request):
        objects = await self.pagination_class.paginate_queryset(
            self.queryset.order_by('id'), request=request
        )
        startT = time.time()
        data = await UniversitySerializer(
            objects, many=True, context={'request': request}
        ).data
        print(f'function time: {time.time() - startT}ms')
        if data:
            response = await self.pagination_class.get_paginated_response(data)
        else:
            response = Response(status=404, data={"errors": [{
                "status": 404, "title": "Not Found",
                "detail": 'There are no universities.'
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
