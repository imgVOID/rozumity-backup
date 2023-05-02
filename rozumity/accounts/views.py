from django.shortcuts import render
from django.http import JsonResponse

from accounts.models import University


def db_populate_universities(request):
    count = 0
    with open('accounts/fixtures/universities_of_ukraine.txt') as data:
        for line in data:
            line = line.strip()
            _, created = University.objects.get_or_create(title=line)
            if created:
                count += 1
    if count:
        response = JsonResponse(status=201, data={
            "success":"true", 
            "text": f'{count} universities have been successfully loaded to the database.'
        })
    else:
        response = JsonResponse(status=409, data={
            "success":"false", 
            "text": f'The database already contains all the provided universities.'
        })
    return response
