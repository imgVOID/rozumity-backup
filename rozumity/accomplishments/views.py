from django.core.exceptions import ObjectDoesNotExist
from rest_framework.response import Response
from rest_framework import permissions
from rest_framework.authentication import SessionAuthentication
from adrf.viewsets import ViewSet
from aiofiles import open

from .models import University
from .serializers import UniversityJSONAPI


class UniversityPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        return True if any((
            request.user.is_authenticated,
            request.method == 'GET' or request.user.is_staff
        )) else False


# TODO: retrieve
class DBUniversity(ViewSet):
    permission_classes=[UniversityPermission]
    authentication_classes = [SessionAuthentication]
    
    async def list(self, request):
        objects = []
        async for university in University.objects.all():
            objects.append(university)
        objects = UniversityJSONAPI(objects, many=True).data
        if objects:
            response = Response(status=200, data={'data': objects})
        else:
            response = Response(status=404, data={"errors": [{
                "status": 404, "title": "Not Found",
                "detail": f'There are no universities for this country.'
            }]})
        return response
    
    async def create(self, request):
        objects = []
        async with open('accomplishments/fixtures/universities_of_ukraine.txt', mode="r", encoding="utf-8") as data:
            async for line in data:
                line = line.strip()
                obj, created = await University.objects.aget_or_create(title=line)
                if created:
                    objects.append(obj)
        objects = UniversityJSONAPI(objects, many=True).data
        if objects:
            response = Response(status=201, data={'data': objects})
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
                try:
                    obj = await University.objects.aget(title=line)
                except ObjectDoesNotExist:
                    pass
                else:
                    university = UniversityJSONAPI(obj).data
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

#TODO: API for apecialities
