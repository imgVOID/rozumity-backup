from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.urls import reverse
from rest_framework.response import Response
from rest_framework.authentication import SessionAuthentication
from adrf.viewsets import ViewSet
from aiofiles import open

from cities_light.models import Country
from .models import University
from .serializers import JSONAPIUniversityManager, UniversityJSONAPISerializer
from .permissions import UniversityPermission


# TODO: retrieve
class UniversityViewSet(ViewSet):
    permission_classes=[UniversityPermission]
    authentication_classes = [SessionAuthentication]
    paginate_by = 100
    
    async def list(self, request, alpha2):
        if len(alpha2) != 2:
            return Response(status=404, data={"errors": [{
                "status": 400, "title": "Bad request",
                "detail": f'Please enter a valid alpha-2 country code.'
            }]})
        try:
            country = await Country.objects.aget(code2=alpha2.upper())
        except ObjectDoesNotExist:
            return Response(status=404, data={"errors": [{
                "status": 404, "title": "Not Found",
                "detail": f'Sorry, but the country is not supported.'
            }]})
        else:
            objects = []
        async for university in University.objects.filter(country=country):
            objects.append(university)
        if objects:
            page_number = request.query_params.get("page", 1)
            paginator = Paginator(objects, self.paginate_by)
            page_obj = paginator.get_page(page_number)
            link = reverse('universities:universities-list', args=['ua'])[:-1]
            data = {'links': {
                "self": link + f'?page={page_obj.number}',
            }}
            if page_obj.has_next():
                data['links']['next'] = link + f'?page={page_obj.next_page_number()}'
            if page_obj.has_previous():
                data['links']['prev'] = link + f'?page={page_obj.previous_page_number()}'
            data['links']["last"] = link + f'?page={paginator.num_pages}'
            data['data'] = await JSONAPIUniversityManager(
                page_obj.object_list, related=country
            ).data
            response = Response(status=201, data=data)
        else:
            response = Response(status=404, data={"errors": [{
                "status": 404, "title": "Not Found",
                "detail": f'There are no universities for {country.name}'
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
    
    async def patch(self, request, alpha2):
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
