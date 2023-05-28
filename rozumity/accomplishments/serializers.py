
from rest_framework import serializers

from rozumity.serializers import JSONAPISerializer, ValidateFieldType


class UniversitySerializer(JSONAPISerializer):
    
    class Attributes(JSONAPISerializer.Attributes):
        title = serializers.CharField(validators=[ValidateFieldType(str)])
    
    class Relationships(JSONAPISerializer.Relationships):
        country = serializers.ListField(child=JSONAPISerializer.Type())


class TestSerializer(JSONAPISerializer):
    
    class Attributes(JSONAPISerializer.Attributes):
        title = serializers.CharField(validators=[ValidateFieldType(str)])
    
    class Relationships(JSONAPISerializer.Relationships):
        city = JSONAPISerializer.Type()
        country = serializers.ListField(child=JSONAPISerializer.Type())
