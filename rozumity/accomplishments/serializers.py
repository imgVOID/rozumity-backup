
from rozumity.serializers import JSONAPI

from .models import University
    

class UniversityJSONAPI(JSONAPI):
    class Meta:
        model = University
        fields = ['id', 'title']
