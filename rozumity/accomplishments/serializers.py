from rest_framework import serializers
#from django.core.validators import MaxValueValidator, MaxLengthValidator
from rozumity.serializers import JSONAPISerializer
from .models import Test


class UniversitySerializer(JSONAPISerializer):
    
    class Attributes(JSONAPISerializer.Attributes):
        title = serializers.CharField(max_length=128)
    
    class Relationships(JSONAPISerializer.Relationships):
        country = JSONAPISerializer.ObjectId(
            view_name='cities-light-api-country-detail'
        )


class TestSerializer(JSONAPISerializer):
    
    class Attributes(JSONAPISerializer.Attributes):
        title = serializers.CharField(max_length=128)
    
    class Relationships(JSONAPISerializer.Relationships):
        city = JSONAPISerializer.ObjectId(
            required=False, view_name='cities-light-api-city-detail'
        )
        country = serializers.ListField(
            required=False, child=JSONAPISerializer.ObjectId(
                view_name='cities-light-api-country-detail'
        ))
    
    class Meta:
        model_type = 'test'
        #model = Test
        #validators = {
        #    'id': MaxValueValidator(0),
        #    'attributes.title': MaxLengthValidator(0),
        #    'relationships.country': MaxLengthValidator(0)
        #    }