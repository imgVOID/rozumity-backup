from rest_framework import serializers
from rozumity.serializers import JSONAPISerializer


class UniversitySerializer(JSONAPISerializer):
    
    class Attributes(JSONAPISerializer.Attributes):
        title = serializers.CharField(max_length=128)
    
    class Relationships(JSONAPISerializer.Relationships):
        country = JSONAPISerializer.Type(
            view_name='cities-light-api-country-detail'
        )


class TestSerializer(JSONAPISerializer):
    
    class Attributes(JSONAPISerializer.Attributes):
        title = serializers.CharField(max_length=128)
    
    class Relationships(JSONAPISerializer.Relationships):
        city = JSONAPISerializer.Type(
            required=False, view_name='cities-light-api-city-detail'
        )
        country = serializers.ListField(
            required=False, child=JSONAPISerializer.Type(
                view_name='cities-light-api-country-detail'
        ))