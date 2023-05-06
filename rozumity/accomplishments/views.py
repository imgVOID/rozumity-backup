from django.core.exceptions import ObjectDoesNotExist
from rest_framework.response import Response
from rest_framework import permissions
from rest_framework.authentication import SessionAuthentication
from adrf.viewsets import ViewSet
from aiofiles import open

from cities_light.models import Country
from .models import University
from .serializers import JSONAPIUniversityManager, UniversityJSONAPISerializer


class UniversityPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return all((
            request.user.is_authenticated,
            request.method == 'GET' or request.user.is_staff
        ))


# TODO: retrieve
class DBUniversity(ViewSet):
    permission_classes=[UniversityPermission]
    authentication_classes = [SessionAuthentication]
    
    async def list(self, request):
        objects = []
        country = await Country.objects.aget(code2='UA')
        async for university in University.objects.filter(country=country):
            objects.append(university)
        if objects:
            data = await JSONAPIUniversityManager(
                objects, related=country
            ).data
            response = Response(status=201, data=data)
        else:
            response = Response(status=404, data={"errors": [{
                "status": 404, "title": "Not Found",
                "detail": f'There are no universities for {country.name}'
            }]})
        return response
    
    async def create(self, request):
        objects = []
        country = await Country.objects.aget(code2='UA')
        async with open('accomplishments/fixtures/universities_of_ukraine.txt', mode="r", encoding="utf-8") as data:
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
    
    async def patch(self, request):
        objects = []
        async with open('accomplishments/fixtures/universities_of_ukraine.txt', mode="r", encoding="utf-8") as data:
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
